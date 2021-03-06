# -*- coding: utf-8 -*-
# Implements periodic checks in order to verify pgp key status and other consistencies:

from datetime import timedelta

from twisted.internet.defer import inlineCallbacks

from globaleaks import models
from globaleaks.handlers.admin.node import db_admin_serialize_node
from globaleaks.handlers.admin.notification import db_get_notification
from globaleaks.handlers.admin.user import db_get_admin_users
from globaleaks.handlers.user import user_serialize_user
from globaleaks.jobs.base import LoopingJob
from globaleaks.orm import transact
from globaleaks.transactions import db_schedule_email
from globaleaks.utils.templating import Templating
from globaleaks.utils.utility import datetime_now, datetime_null
from globaleaks.utils.utility import log


__all__ = ['PGPCheck']


def db_get_expired_or_expiring_pgp_users(session, tids_list):
    threshold = datetime_now() + timedelta(days=15)

    return session.query(models.User).filter(models.User.pgp_key_public != u'',
                                             models.User.pgp_key_expiration != datetime_null(),
                                             models.User.pgp_key_expiration < threshold,
                                             models.UserTenant.user_id == models.User.id,
                                             models.UserTenant.tenant_id.in_(tids_list))


class PGPCheck(LoopingJob):
    interval = 24 * 3600
    monitor_interval = 5 * 60

    def get_start_time(self):
        current_time = datetime_now()
        return (3600 * 24) - (current_time.hour * 3600) - (current_time.minute * 60) - current_time.second

    def prepare_admin_pgp_alerts(self, session, tid, expired_or_expiring):
        for user_desc in db_get_admin_users(session, tid):
            user_language = user_desc['language']

            data = {
                'type': u'admin_pgp_alert',
                'node': db_admin_serialize_node(session, tid, user_language),
                'notification': db_get_notification(session, tid, user_language),
                'users': expired_or_expiring,
                'user': user_desc,
            }

            subject, body = Templating().get_mail_subject_and_body(data)

            db_schedule_email(session, tid, user_desc['mail_address'], subject, body)

    def prepare_user_pgp_alerts(self, session, tid, user_desc):
        user_language = user_desc['language']

        data = {
            'type': u'pgp_alert',
            'node': db_admin_serialize_node(session, tid, user_language),
            'notification': db_get_notification(session, tid, user_language),
            'user': user_desc
        }

        subject, body = Templating().get_mail_subject_and_body(data)

        db_schedule_email(session, tid, user_desc['mail_address'], subject, body)

    @transact
    def perform_pgp_validation_checks(self, session):
        tenant_expiry_map = {1: []}

        for user in db_get_expired_or_expiring_pgp_users(session, self.state.tenant_cache.keys()):
            user_desc = user_serialize_user(session, user, user.language)
            tenant_expiry_map.setdefault(user.tid, []).append(user_desc)

            log.info('Removing expired PGP key of: %s', user.username, tid=user.tid)
            if user.pgp_key_expiration < datetime_now():
                user.pgp_key_public = ''
                user.pgp_key_fingerprint = ''
                user.pgp_key_expiration = datetime_null()

        for tid, expired_or_expiring in tenant_expiry_map.items():
            for user_desc in expired_or_expiring:
                self.prepare_user_pgp_alerts(session, tid, user_desc)

            if self.state.tenant_cache[tid].notification.disable_admin_notification_emails:
                continue

            if expired_or_expiring:
                self.prepare_admin_pgp_alerts(session, tid, expired_or_expiring)

    def operation(self):
        return self.perform_pgp_validation_checks()

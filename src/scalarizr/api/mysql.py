'''
Created on Dec 04, 2011

@author: marat
'''
from __future__ import with_statement

import threading

from scalarizr import handlers, rpc, storage2
from scalarizr.services import mysql as mysql_svc
from scalarizr.services.mysql2 import __mysql__
from scalarizr.util.cryptotool import pwgen


class MySQLAPI(object):
    """
    @xxx: reporting is an anal pain
    """

    error_messages = {
        'empty': "'%s' can't be blank",
        'invalid': "'%s' is invalid, '%s' expected"
    }

    def __init__(self):
        self._mysql_init = mysql_svc.MysqlInitScript()

    @rpc.service_method
    def grow_volume(self, volume, growth, async=False):
        self._check_invalid(volume, 'volume', dict)
        self._check_empty(volume.get('id'), 'volume.id')

        def do_grow():
            vol = storage2.volume(volume)
            self._mysql_init.stop('Growing data volume')
            try:
                growed_vol = vol.grow(**growth)
                __mysql__['volume'] = dict(growed_vol)
                return dict(growed_vol)
            finally:
                self._mysql_init.start()

        if async:
            txt = 'Grow MySQL/Percona data volume'
            op = handlers.operation(name=txt)

            def block():
                op.define()
                with op.phase(txt):
                    with op.step(txt):
                        data = do_grow()
                op.ok(data=data)
            threading.Thread(target=block).start()
            return op.id

        else:
            return do_grow()

    def _check_invalid(self, param, name, type_):
        assert isinstance(param, type_), \
            self.error_messages['invalid'] % (name, type_)

    def _check_empty(self, param, name):
        assert param, self.error_messages['empty'] % name

    @rpc.service_method
    def reset_password(self, new_password=None):
        """ Reset password for MySQL user 'scalr'. Return new password """
        if not new_password:
            new_password = pwgen(20)
        mysql_cli = mysql_svc.MySQLClient()
        mysql_cli.set_user_password('scalr', 'localhost', new_password)
        mysql_cli.flush_privileges()
        __mysql__['root_password'] = new_password
        return new_password

    @rpc.service_method
    def replication_status(self):
        '''
        For Master:
        {'master': {
            'status': 'up',
            'log_file': 'binlog.000015', 
            'log_pos': 165
        }}

        For Slave:
        {'slave': {
            'status': 'up',
            'master_log_file': 'binlog.000015',
            'read_master_log_file': 165,
            'relay_log_file': 'relay-log.00023',
            'relay_log_pos': 231,
            'seconds_behind_master': 52,
            ... # output of SHOW SLAVE STATUS
        }}
        '''
        pass

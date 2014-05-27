# pylint: disable=R0924

import urllib2
import json
import logging
import re
import os
import sys
import posixpath
import glob
import time
import operator
from multiprocessing import pool as process_pool

from scalarizr import linux
if linux.os.windows:
    from win32com import client as comclient
    from scalarizr.util import coinitialized


LOG = logging.getLogger(__name__)


class Error(Exception):
    pass

class NoProviderError(Error):
    pass

class NoUserDataError(Error):
    pass


class Userdata(dict):
    @classmethod
    def from_string(cls, data):
        return Userdata(re.findall("([^=]+)=([^;]*);?", data))


class VoteCapabilityDict(dict):
    def incr_each(self, value=1):
        for key in self:
            self[key] += value

    def decr_each(self, value=1):
        for key in self:
            self[key] -= value


class VoteDict(dict):
    def __getitem__(self, key):
        try:
            return super(VoteDict, self).__getitem__(key)
        except KeyError:
            for cls, value in self.iteritems():
                if cls.__class__.__name__ == key:
                    return value
            raise


class Metadata(object):

    class NoDataPvd(object):
        def __init__(self, metadata):
            self.metadata = metadata

        def instance_id(self):
            return self.metadata['user_data']['serverid']

        def user_data(self):
            raise NoUserDataError()


    def __init__(self, providers=None, capabilities=None):
        self._cache = {}
        self.provider_for_capability = {}
        self._nodata_pvd = self.NoDataPvd(self)
        self._providers_resolved = False
        if not providers:
            if linux.os.windows:
                providers = [
                    Ec2Pvd(),
                    OpenStackQueryPvd(),
                    FileDataPvd('C:\\Program Files\\Scalarizr\\etc\\private.d\\.user-data')
                ]
            else:
                providers = [
                    Ec2Pvd(),
                    GcePvd(),
                    OpenStackQueryPvd(),
                    OpenStackXenStorePvd(),
                    CloudStackPvd(),
                    FileDataPvd('/etc/.scalr-user-data'),
                    FileDataPvd('/etc/scalr/private.d/.user-data')
                ]
        self.providers = providers
        self.capabilities = capabilities or ['instance_id', 'user_data']


    def _resolve_once_providers(self):
        if self._providers_resolved:
            return  
        votes = VoteDict()
        for pvd in self.providers:
            votes[pvd] = VoteCapabilityDict.fromkeys(self.capabilities, 0)
        def vote(pvd):
            try:
                pvd.vote(votes)
            except:
                LOG.debug('{0}.vote raised: {1}'.format(
                        pvd.__class__.__name__, sys.exc_info()[1]))
        pool = process_pool.ThreadPool(processes=len(self.providers))
        try:
            pool.map(vote, self.providers)
        finally:
            pool.close()
        for cap in self.capabilities:
            cap_votes = ((pvd, votes[pvd][cap]) for pvd in votes)
            cap_votes = sorted(cap_votes, key=operator.itemgetter(1))
            pvd, vote = cap_votes[-1]
            if not vote:
                pvd = self._nodata_pvd
            LOG.debug("provider for '{0}': {1}".format(cap, pvd))
            self.provider_for_capability[cap] = pvd
        self._providers_resolved = True

    def __getitem__(self, capability):
        self._resolve_once_providers()
        if not capability in self._cache:
            try:
                pvd = self.provider_for_capability[capability]
            except KeyError:
                msg = "Can't find a provider for '{0}'".format(capability)
                raise NoProviderError(msg)
            else:
                self._cache[capability] = getattr(pvd, capability)()
        return self._cache[capability]


class Provider(object):
    LOG = logging.getLogger(__name__)
    HTTP_TIMEOUT = 10
    base_url = None

    def vote(self, votes):
        raise NotImplementedError()

    def try_url(self, url=None, rel=None, headers=None):
        try:
            return self.get_url(url, rel, headers)
        except:
            if rel:
                url = posixpath.join(url or self.base_url, rel)
            self.LOG.debug('Try {0!r}: {1}'.format(url, sys.exc_info()[1]))
            return False

    def get_url(self, url=None, rel=None, headers=None):
        if rel:
            url = posixpath.join(url or self.base_url, rel)
        return urllib2.urlopen(urllib2.Request(url, headers=headers or {}), 
                timeout=self.HTTP_TIMEOUT).read().strip()

    def try_file(self, path):
        if not os.path.exists(path):
            self.LOG.debug('Try {0!r}: not exists'.format(path))
        elif not os.access(path, os.R_OK):
            self.LOG.debug('Try {0!r}: not readable'.format(path))
        else:
            return True

    def get_file(self, path):
        with open(path) as fp:
            return fp.read().strip() 


class Ec2Pvd(Provider):
    LOG = logging.getLogger(__name__ + '.ec2')

    def __init__(self):
        self.base_url = 'http://169.254.169.254/latest'
        self._instance_id = None
        self._user_data = None

    def vote(self, votes):
        self._instance_id = self.try_url(rel='meta-data/instance-id')
        if self._instance_id != False:
            self.LOG.debug('matched instance_id')
            votes[self]['instance_id'] += 1
            self._user_data = self.try_url(rel='user-data')
            if self._user_data != False:
                self.LOG.debug('matched user_data')
                votes[self]['user_data'] += 1

    def instance_id(self):
        return self._instance_id

    def user_data(self):
        return Userdata.from_string(self._user_data)
           

class GcePvd(Provider):
    LOG = logging.getLogger(__name__ + '.gce')

    def __init__(self):
        self.base_url = 'http://metadata/computeMetadata/v1'    

    def get_url(self, url=None, rel=None, headers=None):
        return super(GcePvd, self).get_url(url, rel, 
                headers={'X-Google-Metadata-Request': 'True'})

    def vote(self, votes):
        if self.try_url(self.base_url):
            self.LOG.debug('matched')
            votes[self].incr_each()

    def instance_id(self):
        return self.get_url(rel='instance/id')

    def user_data(self):
        return Userdata.from_string(
                self.get_url(rel='instance/attributes/scalr'))
        

class OpenStackQueryPvd(Provider):
    LOG = logging.getLogger(__name__ + '.openstack-query')

    def __init__(self, 
            metadata_json_url='http://169.254.169.254/openstack/latest/meta_data.json'):
        self.metadata_json_url = metadata_json_url
        self._cache = {}

    def vote(self, votes):
        meta = self.try_url(self.metadata_json_url)
        if meta:
            self.LOG.debug('matched meta_data.json')
            self._cache = json.loads(meta)
            votes[self]['instance_id'] += 1
            votes['Ec2Pvd']['instance_id'] -= 1
            if 'CloudStackPvd' in votes:
                votes['CloudStackPvd']['instance_id'] -= 1
        if 'meta' in self._cache:
            self.LOG.debug('matched user_data in meta_data.json')
            votes[self]['user_data'] += 1
            votes['Ec2Pvd']['user_data'] -= 1
            if 'CloudStackPvd' in votes:
                votes['CloudStackPvd']['user_data'] -= 1

    def instance_id(self):
        return self._cache['instance_id']

    def user_data(self):
        return self._cache['meta']


class OpenStackXenStorePvd(Provider):
    LOG = logging.getLogger(__name__ + '.openstack-xenbus')

    def vote(self, votes):
        if self.try_file('/proc/xen/xenbus') and linux.which('xenstore-ls') \
                and linux.which('nova-agent'):
            self.LOG.debug('matched user_data')
            votes[self]['user_data'] += 1
            votes['Ec2Pvd']['user_data'] -= 1

    def user_data(self):
        keyvalue_re = re.compile(r'([^\s]+)\s+=\s+\"{2}([^\"]+)\"{2}')
        ret = []
        out = linux.system((linux.which('xenstore-ls'), 'vm-data/user-metadata'))[0]
        for line in out.splitlines():
            m = keyvalue_re.search(line)
            if m:
                ret.append(m.groups())
        return dict(ret)


class CloudStackPvd(Provider):
    LOG = logging.getLogger(__name__ + '.cloudstack')

    def __init__(self, 
            dhcp_server=None, 
            dhcp_leases_pattern='/var/lib/dhc*/dhclient*.leases'):
        self.dhcp_server = dhcp_server
        self.dhcp_leases_pattern = dhcp_leases_pattern
        self._instance_id = None
        self._user_data = None

    @property
    def base_url(self):
        if not self.dhcp_server:
            self.dhcp_server = self.get_dhcp_server(self.dhcp_leases_pattern)
            self.LOG.debug('Use DHCP server: %s', self.dhcp_server)
        return 'http://{0}/latest'.format(self.dhcp_server)

    @classmethod
    def get_dhcp_server(cls, leases_pattern=None):
        router = None
        try:
            leases_file = glob.glob(leases_pattern)[0]
            cls.LOG.debug('Use DHCP leases file: %s', leases_file)
        except IndexError:
            msg = "Pattern {0} doesn't matches any leases files".format(leases_pattern)
            raise Error(msg)
        for line in open(leases_file):
            if 'dhcp-server-identifier' in line:
                router = filter(None, line.split(';')[0].split(' '))[2]
        return router

    def vote(self, votes):
        self._instance_id = self.try_url(rel='instance-id')
        if self._instance_id != False:
            self.LOG.debug('matched instance_id')
            votes[self]['instance_id'] += 1
            votes['Ec2Pvd']['instance_id'] -= 1
            self._user_data = self.try_url(rel='user-data')
            if self._user_data != False:
                self.LOG.debug('matched user_data')
                votes[self]['user_data'] += 1
                votes['Ec2Pvd']['user_data'] -= 1

    def instance_id(self):
        if len(self._instance_id) == 36:
            # uuid-like (CloudStack 3)
            return self._instance_id
        else:
            # CloudStack 2 / IDCF
            return self._instance_id.split('-')[2]

    def user_data(self):
        return Userdata.from_string(self._user_data)


class FileDataPvd(Provider):
    LOG = logging.getLogger(__name__ + '.file')

    def __init__(self, filename):
        self.filename = filename

    def vote(self, votes):
        if self.try_file(self.filename):
            fmt = '%Y-%m-%dT%H:%M:%S'
            times_str = '(mtime: {0!r}, boot_time: {1!r})'.format(
                    time.strftime(fmt, time.localtime(os.stat(self.filename).st_mtime)),
                    time.strftime(fmt, time.localtime(boot_time())))
            # user-data file is not stale if it was injected
            #  1) not more then a minute before OS boot (with nova)
            #  2) after boot (with novaagent)
            if os.stat(self.filename).st_mtime > boot_time() - 60:
                self.LOG.debug('matched user_data in file {0!r} {1}'.format(
                        self.filename, times_str))
                votes[self]['user_data'] += 1
            else:
                self.LOG.debug(('skipping user_data file {0!r}, '
                        'cause it was modified before os boot time {1}').format(
                        self.filename, times_str))

    def __repr__(self):
        return '<FileDataPvd at {0} filename={1}>'.format(
                hex(id(self)), self.filename)

    def user_data(self):
        return Userdata.from_string(self.get_file(self.filename))


_boot_time = None
def boot_time():
    # pylint: disable=W0603
    global _boot_time
    if not _boot_time:
        if linux.os.windows:
            @coinitialized
            def get_boot_time():
                wmi = comclient.GetObject('winmgmts:')
                win_os = next(iter(wmi.InstancesOf('Win32_OperatingSystem')))
                local_time, tz_op, tz_hh60mm = re.split(r'(\+|\-)', win_os.LastBootUpTime)
                local_time = local_time.split('.')[0]
                local_time = time.mktime(time.strptime(local_time, '%Y%m%d%H%M%S'))
                tz_seconds = int(tz_hh60mm) * 60
                if tz_op == '+':
                    return local_time + tz_seconds
                else:
                    return local_time - tz_seconds
        else:
            def get_boot_time():
                with open('/proc/uptime') as fp:
                    uptime = float(fp.read().strip().split()[0])
                return time.time() - uptime
        _boot_time = get_boot_time()
    return _boot_time

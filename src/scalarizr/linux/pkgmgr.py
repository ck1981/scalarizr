'''
Created on Aug 28, 2012

@author: marat
'''

import logging
import glob
import re
import os
import string
import time
import urllib
import subprocess
import shutil
import tempfile
import distutils.version

from scalarizr import linux, util
from scalarizr.linux import coreutils
from urlparse import urlparse

LOG = logging.getLogger(__name__)


class Repository(object):
    filename_tpl = None
    config_tpl = '{url}'

    def __init__(self, name, url):
        kwds = {
            'name': name,
            'url': url,
            'arch': linux.os['arch']
        }
        self.name = name
        self.url = url.format(**kwds)
        self.filename = self.filename_tpl.format(**kwds)
        self.config = self.config_tpl.format(**kwds)

    def ensure(self):
        assert self.filename and self.config
        LOG.info('Creating repository at: %s', self.filename)
        with open(self.filename, 'w+') as fp:
            fp.write(self.config)

    def update_config_file(self, linefn):
        tmpname = '%s.tmp' % self.filename
        dst = open(tmpname, 'w')
        try:
            for line in open(self.filename, 'r'):
                dst.write(linefn(line))
            dst.close()
        except:
            os.remove(tmpname)
        else:
            shutil.move(tmpname, self.filename)
        
    def _comment(self, line):
        return '#' + line
    
    def _uncomment(self, line):
        return line[1:] if line.startswith('#') else line
    
    def disable(self):
        if self.enabled():
            self.update_config_file(self._comment)
    
    def enable(self):
        if not self.enabled():
            self.update_config_file(self._uncomment)

    def enabled(self):
        return not all(line.startswith('#') for line in open(self.filename))


class PackageMgr(object):

    def install(self, name, version=None, updatedb=False, **kwds):
        ''' Installs a `version` of package `name` '''
        raise NotImplementedError()

    def remove(self, name, purge=False):
        ''' Removes package with given name. '''
        raise NotImplementedError()

    def info(self, name):
        '''
        Returns info about package
        Example:
                {'installed': '2.6.7-ubuntu1',
                'candidate': '2.6.7-ubuntu5'}
        installed is None, if package not installed
        candidate is None, if latest version of package is installed
        '''
        raise NotImplementedError()

    def updatedb(self):
        ''' Updates package manager internal database '''
        raise NotImplementedError()

    def repos(self):
        ''' List enabled repositories '''
        raise NotImplementedError()

    def version_cmp(self, name_1, name_2):
        ''' Compares 2 package versions '''
        raise NotImplementedError()

    def installed(self, name, version=None, updatedb=False):
        '''
        Ensure that package installed
        '''
        if updatedb:
            self.updatedb()

        installed = self.info(name)['installed']
        if not installed:
            LOG.info('Installing %s=%s', name, version)
            self.install(name, version)

    def latest(self, name, updatedb=True):
        '''
        Ensure that latest version of package installed
        '''
        if updatedb:
            self.updatedb()

        info_dict = self.info(name)
        candidate = info_dict['candidate']
        installed = info_dict['installed']

        if candidate or not installed:
            LOG.info('Installing %s=%s', name, candidate)
            self.install(name, candidate)

    def removed(self, name, purge=False):
        '''
        Ensure that package removed (purged)
        '''
        installed = self.info(name)['installed']
        if purge or installed:
            if installed:
                LOG.info('Uninstalling %s=%s', name, installed)
            else:
                LOG.info('Uninstalling %s', name)
            self.remove(name, purge)


class AptPackageMgr(PackageMgr):
    def apt_get_command(self, command, **kwds):
        kwds.update(env={'DEBIAN_FRONTEND': 'noninteractive',
                        'DEBIAN_PRIORITY': 'critical',
                        'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games'},
                        raise_exc=False
        )
        for _ in range(18):  # timeout approx 3 minutes
            out, err, code = linux.system(('/usr/bin/apt-get',
                                            '-q', '-y', '--force-yes',
                                            '-o Dpkg::Options::=--force-confold') + \
                                            tuple(filter(None, command.split())), **kwds)
            if code:
                if 'is another process using it?' in err \
                    or 'Could not get lock' in err:
                    LOG.debug('Could not get dpkg lock (perhaps, another process is using it.)')
                    time.sleep(10)
                    continue
                else:
                    raise linux.LinuxError('Apt-get command failed. Out: %s \nErrors: %s' % (out, err))

            else:
                return out, err, code

        raise Exception('Apt-get command failed: dpkg is being used by another process')

    def apt_cache_command(self, command, **kwds):
        return linux.system(('/usr/bin/apt-cache',) + tuple(filter(None, command.split())), **kwds)


    def apt_policy(self, name):
        installed_re = re.compile(r'^\s{2}Installed: (.+)$')
        candidate_re = re.compile(r'^\s{2}Candidate: (.+)$')
        installed = candidate = None

        for line in self.apt_cache_command('policy %s' % name)[0].splitlines():
            m = installed_re.match(line)
            if m:
                installed = m.group(1)
                if installed == '(none)':
                    installed = None
                continue

            m = candidate_re.match(line)
            if m:
                candidate = m.group(1)
                continue

        return installed, candidate

    def updatedb(self):
        try:
            coreutils.clean_dir('/var/lib/apt/lists/partial', recursive=False)
        except OSError:
            pass
        path = '/var/lib/apt/lists'
        for name in os.listdir(path):
            filename = os.path.join(path, name)
            if name != 'lock' and os.path.isfile(filename):
                os.remove(filename)
        self.apt_get_command('update')

    def install(self, name, version=None, updatedb=False, **kwds):
        if version:
            name += '=%s' % version
        if updatedb:
            self.updatedb()
        self.apt_get_command('install %s' % name, raise_exc=True)

    def remove(self, name, purge=False):
        command = 'purge' if purge else 'remove'
        self.apt_get_command('%s %s' % (command, name), raise_exc=True)

    def info(self, name):
        installed, candidate = self.apt_policy(name)
        return {'installed': installed,
                        'candidate': candidate if installed != candidate else None}

    def repos(self):
        files = glob.glob('/etc/apt/sources.list.d/*.list')
        names = [os.path.basename(os.path.splitext(f)[0]) for f in files]
        return names

    def version_cmp(self, name_1, name_2):
        if name_1 == name_2:
            return 0
        return_code = linux.system(('/usr/bin/dpkg', '--compare-versions', str(name_1), '>', str(name_2)), 
                    raise_exc=False)[2]
        return 1 if not return_code else -1

class AptRepository(Repository):
    filename_tpl = '/etc/apt/sources.list.d/{name}.list'
    config_tpl = 'deb {url}'


class RpmVersion(object):

    def __init__(self, version):
        self.version = version
        self._re_not_alphanum = re.compile(r'^[^a-zA-Z0-9]+')
        self._re_digits = re.compile(r'^(\d+)')
        self._re_alpha = re.compile(r'^([a-zA-Z]+)')

    def __iter__(self):
        ver = self.version
        while ver:
            ver = self._re_not_alphanum.sub('', ver)
            if not ver:
                break

            if ver and ver[0].isdigit():
                token = self._re_digits.match(ver).group(1)
            else:
                token = self._re_alpha.match(ver).group(1)

            yield token
            ver = ver[len(token):]

        raise StopIteration()

    def __cmp__(self, other):
        iter2 = iter(other)

        for tok1 in self:
            try:
                tok2 = iter2.next()
            except StopIteration:
                return 1

            if tok1.isdigit() and tok2.isdigit():
                c = cmp(int(tok1), int(tok2))
                if c != 0:
                    return c
            elif tok1.isdigit() or tok2.isdigit():
                return 1 if tok1.isdigit() else -1
            else:
                c = cmp(tok1, tok2)
                if c != 0:
                    return c

        try:
            iter2.next()
            return -1
        except StopIteration:
            return 0


class YumPackageMgr(PackageMgr):

    def yum_command(self, command, **kwds):
        # explicit exclude was added after yum tried to install iptables.i686
        # on x86_64 amzn
        exclude = ()
        if linux.os["arch"] == "x86_64":
            exclude = (
                "--exclude", "*.i386",
                "--exclude", "*.i486",
                "--exclude", "*.i686",
            )
        elif linux.os["arch"] == "i386":
            exclude = ("--exclude", "x86_64")

        return linux.system((('/usr/bin/yum', '-d0', '-y') + tuple(filter(None, command.split())) + exclude), **kwds)


    def yum_list(self, name):
        out = self.yum_command('list --showduplicates %s' % name)[0].strip()

        version_re = re.compile(r'[^\s]+\s+([^\s]+)')
        lines = map(string.strip, out.splitlines())

        try:
            line = lines[lines.index('Installed Packages')+1]
            installed = version_re.match(line).group(1)
        except ValueError:
            installed = None

        if 'Available Packages' in lines:
            versions = [version_re.match(line).group(1) for line in lines[lines.index('Available Packages')+1:]]
        else:
            versions = []

        return installed, versions


    def updatedb(self):
        self.yum_command('clean expire-cache')


    def install(self, name, version=None, updatedb=False, **kwds):
        if version:
            name += '-%s' % version
        if updatedb:
            self.updatedb()
        self.yum_command('install %s' %  name, raise_exc=True)

    def localinstall(self, name):
        def do_localinstall(filename):
            self.yum_command('localinstall --nogpgcheck %s' % filename, raise_exc=True)

        if name.startswith('http://'):
            filename = os.path.join('/tmp', os.path.basename(name))
            urllib.urlretrieve(name, filename)
            try:
                do_localinstall(filename)
            finally:
                os.remove(filename)
        else:
            do_localinstall(name)
       

    def remove(self, name, purge=False):
        self.yum_command('remove '+name, raise_exc=True)


    def info(self, name):
        installed, candidates = self.yum_list(name)
        return {'installed': installed,
                'candidate': candidates[-1] if candidates else None}

    def repos(self):
        ret = []
        repo_re = re.compile(r'Repo-id\s+:\s(.*)')
        out = linux.system(('/usr/bin/yum', 'repolist', '--verbose'))[0]
        for line in out.splitlines():
            m = repo_re.search(line)
            if m:
                ret.append(m.group(1))
        return map(string.lower, ret)

    def version_cmp(self, name_1, name_2):
        return cmp(RpmVersion(name_1), RpmVersion(name_2))


class YumRepository(Repository):
    filename_tpl = '/etc/yum.repos.d/{name}.repo'
    config_tpl = (
        '[{name}]\n'
        'name={name}\n'
        'baseurl={url}\n'
        'enabled=1\n'
        'gpgcheck=0\n'
    )


class RpmPackageMgr(PackageMgr):

    def rpm_command(self, command, **kwds):
        return linux.system(['/usr/bin/rpm', ] + filter(None, command.split()), **kwds)

    def install(self, name, version=None, updatedb=False, **kwds):
        ''' Installs a package from file or url with `name' '''
        self.rpm_command('-Uvh '+name, raise_exc=True, **kwds)

    def remove(self, name, purge=False):
        self.rpm_command('-e '+name, raise_exc=True)

    def _version_from_name(self, name):
        ''' Returns version of package that contains in its name
                Example:
                        name = vim-common-7.3.682-1.fc17.x86_64
                        version = 7.3.682-1.fc17.x86_64
        '''
        # TODO: remove architecture info from version string
        name = urlparse(name).path.split('/')[-1]
        name = name.replace('.rpm', '')

        version = re.findall(r'-[0-9][^-]*\..*', name)[0][1:]
        return version

    def info(self, name):
        name = urlparse(name).path.split('/')[-1]
        name = name.replace('.rpm', '')

        out, _, code = self.rpm_command('-q '+name, raise_exc=False)
        installed = not code
        installed_version = self._version_from_name(out)

        return {'installed': installed_version if installed else None,
                'candidate': None}

    def updatedb(self):
        pass


if linux.os.windows_family:

    class WinPackageMgr(object):
        SOURCES_DIR = os.path.join(util.reg_value('InstallDir'), 'etc')

        def __init__(self):
            self.index = None
        
        def install(self, name, version=None, updatedb=False, **kwds):
            ''' Installs a `version` of package `name` '''
            if not self.index or updatedb:
                self.updatedb()
            packageurl = self.index[name]  # simplification. candidate is always the one.

            tmp_dir = tempfile.mkdtemp()
            try:
                packagefile = os.path.join(tmp_dir, os.path.basename(packageurl))
                LOG.info('Downloading %s', packageurl)
                urllib.urlretrieve(packageurl, packagefile)

                LOG.info('Executing installer')
                p = subprocess.Popen('start "Installer" /wait "%s" /S' % packagefile, shell=True)
                out, err = p.communicate()
                LOG.debug('out: %s', out)
                LOG.debug('err: %s', err)
                if p.returncode:
                    msg = 'Installation failed. <out>: %s, <err>: %s' % (out, err)
                    raise Exception(msg)
                LOG.info('Done')
            finally:
                shutil.rmtree(tmp_dir)
        
        def installed_version(self, name):
            ''' Return installed package version '''
            return '{0}-{1}'.format(util.reg_value('DisplayVersion'), util.reg_value('DisplayRelease'))

        
        def _join_packages_str(self, sep, name, version, *args):
            packages = [(name, version)]
            if args:
                for i in xrange(0, len(args), 2):
                    packages.append(args[i:i+2])
            format = '%s' + sep +'%s'
            return ' '.join(format % p for p in packages)       
        
        def updatedb(self):
            tmp_dir = tempfile.mkdtemp()

            self.index = {}
            try:        
                LOG.debug('Scan package sources dir')
                for sourcesfile in glob.glob(os.path.join(self.SOURCES_DIR, '*.winrepo')):
                    LOG.debug('Process sources file %s', sourcesfile)
                    urls = open(sourcesfile).read().splitlines()
                    urls = map(string.strip, urls)
                    urls = filter(lambda u: u and not u.startswith('#'), urls)
                    for url in urls:
                        dst = os.path.join(tmp_dir, url.replace('/', '_'))
                        src = url + '/index'
                        LOG.debug('Fetching index file %s', src)
                        urllib.urlretrieve(src, dst)

                        with open(dst) as fp:
                            for line in fp:
                                colums = map(string.strip, line.split(' '))
                                colums = filter(None, colums)
                                package = colums[0]
                                packagefile = colums[1]
                                self.index[package] = url + '/' + packagefile
            finally:
                shutil.rmtree(tmp_dir)

        def info(self, name):
            if not self.index:
                self.updatedb()

            try:
                candidate = os.path.basename(self.index[name])
                candidate = candidate.split('_', 1)[1].rsplit('.', 2)[0]
            except KeyError:
                candidate = None
            try:
                installed = self.installed_version(name)
            except:
                installed = None

            LOG.debug('Package %s info. installed: %s, candidate: %s', name, installed, candidate)
            return {
                'installed': installed,
                'candidate': candidate
            }

        def version_cmp(self, name_1, name_2):
            return cmp(distutils.version.LooseVersion(name_1), 
                distutils.version.LooseVersion(name_2))


    class WinRepository(Repository):
        filename_tpl = os.path.join(WinPackageMgr.SOURCES_DIR, '{name}.winrepo')
        config_tpl = '{url}'


def package_mgr():
    if linux.os['family'] == 'Windows':
        return WinPackageMgr()
    elif linux.os['family'] in ('RedHat', 'Oracle'):
        return YumPackageMgr()
    return AptPackageMgr()


def repository(*args, **kwds):
    cls = None
    if linux.os['family'] == 'Windows':
        cls = WinRepository
    elif linux.os['family'] in ('RedHat', 'Oracle'):
        cls = YumRepository
    else:
        cls = AptRepository
    return cls(*args, **kwds)


EPEL_RPM_URL = 'http://download.fedoraproject.org/pub/epel/6/i386/epel-release-6-7.noarch.rpm'
def epel_repository():
    '''
    Ensure EPEL repository for RHEL based servers.
    Figure out linux.os['arch'], linux.os['release']
    '''
    if linux.os['family'] not in ('RedHat', 'Oracle'):
        return

    mgr = RpmPackageMgr()
    installed = mgr.info(EPEL_RPM_URL)['installed']
    if not installed:
        mgr.install(EPEL_RPM_URL)


def apt_source(name, sources, gpg_keyserver=None, gpg_keyid=None):
    '''
    @param sources: list of apt sources.list entries.
    Example:
            ['deb http://repo.percona.com/apt ${codename} main',
            'deb-src http://repo.percona.com/apt ${codename} main']
            All ${var} templates should be replaced with
            scalarizr.linux.os['var'] substitution
    if gpg_keyserver:
            apt-key adv --keyserver ${gpg_keyserver} --recv ${gpg_keyid}
    Creates file /etc/apt/sources.list.d/${name}
    '''
    if linux.os['family'] in ('RedHat', 'Oracle'):
        return

    def _vars(s):
        vars_ = re.findall('\$\{.+?\}', s)
        return map((lambda name: name[2:-1]), vars_) #2 is len of '${'

    def _substitude(s):
        for var in _vars(s):
            s = s.replace('${'+var+'}', linux.os[var])
        return s

    prepared_sources = map(_substitude, sources)
    with open('/etc/apt/sources.list.d/' + name, 'w+') as fp:
        fp.write('\n'.join(prepared_sources))

    if gpg_keyserver and gpg_keyid:
        if gpg_keyid not in linux.system(('apt-key', 'list'))[0]:
            linux.system(('apt-key', 'adv',
                                      '--keyserver', gpg_keyserver,
                                      '--recv', gpg_keyid),
                                     raise_exc=False)

# Deprecated functions

def updatedb():
    return package_mgr().updatedb()

def installed(name, version=None, updatedb=False):
    return package_mgr().installed(name, version, updatedb)

def latest(name, updatedb=True):
    return package_mgr().latest(name, updatedb)

def removed(name, purge=False):
    return package_mgr().removed(name, purge)


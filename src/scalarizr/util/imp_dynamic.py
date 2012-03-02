'''
Created on 29.02.2012

@author: sam
'''

from scalarizr.util import disttool	
from scalarizr.util import system2
from scalarizr.libs import metaconf


import logging
import re
import string
import sys, os
import imp
try:
	import ConfigParser as configparser
except:
	import configparser as configparser

LOG = logging.getLogger(__name__)

'''----------------------------------
# Package managers
----------------------------------'''
class PackageMgr(object):
	def __init__(self):
		self.proc = None

	def install(self, name, version, *args):
		''' Installs a `version` of package `name` '''
		raise NotImplemented()

	def _join_packages_str(self, sep, name, version, *args):
		packages = [(name, version)]
		if args:
			for i in xrange(0, len(args), 2):
				packages.append(args[i:i+2])
		format = '%s' + sep +'%s'
		return ' '.join(format % p for p in packages)		

	def check_update(self, name):
		''' Returns info for package `name` '''
		raise NotImplemented()

	def candidates(self, name):
		''' Returns all available installation candidates for `name` '''
		raise NotImplemented()


class AptPackageMgr(PackageMgr):
	def apt_get_command(self, command, **kwds):
		kwds.update(env={
			'DEBIAN_FRONTEND': 'noninteractive', 
			'DEBIAN_PRIORITY': 'critical',
			'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games'
		})
		return system2(('apt-get', '-q', '-y') + tuple(filter(None, command.split())), **kwds)

	def apt_cache_command(self, command, **kwds):
		return system2(('apt-cache',) + tuple(filter(None, command.split())), **kwds)

	def candidates(self, name):
		version_available_re = re.compile(r'^\s{5}([^\s]+)\s{1}')
		version_installed_re = re.compile(r'^\s{1}\*\*\*|s{1}([^\s]+)\s{1}')
		
		self.apt_get_command('update')
		
		versions = []
		
		for line in self.apt_cache_command('policy %s' % name)[0].splitlines():
			m = version_available_re.match(line)
			if m:
				versions.append(m.group(1))
			m = version_installed_re.match(line)
			if m:
				break

		versions.reverse()
		return versions


	def check_update(self, name):
		installed_re = re.compile(r'^\s{2}Installed: (.+)$')
		candidate_re = re.compile(r'^\s{2}Candidate: (.+)$')
		installed = candidate = None

		self.apt_get_command('update')
		
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
			
		if candidate and installed:
			if not system2(('dpkg', '--compare-versions', candidate, 'gt', installed), raise_exc = False)[2]:
				return candidate
	
	def install(self, name, version, *args):
		self.apt_get_command('install %s' % self._join_packages_str('=', name, version, *args), raise_exc=True)


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
		return system2((('yum', '-d0', '-y') + tuple(filter(None, command.split()))), **kwds)

	def rpm_ver_cmp(self, v1, v2):
		return cmp(RpmVersion(v1), RpmVersion(v2))
	
	def candidates(self, name):
		self.yum_command('clean expire-cache')
		out = self.yum_command('list --showduplicates %s' % name)[0].strip()
		
		version_re = re.compile(r'[^\s]+\s+([^\s]+)')
		lines = map(string.strip, out.splitlines())
		
		try:
			line = lines[lines.index('Installed Packages')+1]
			installed = version_re.match(line).group(1)
		except ValueError:
			installed = None

		versions = [version_re.match(line).group(1) for line in lines[lines.index('Available Packages')+1:]]
		if installed:
			versions = [v for v in versions if self.rpm_ver_cmp(v, installed) > 0]

		return versions


	def check_update(self, name):
		self.yum_command('clean expire-cache')
		out, _, code = self.yum_command('check-update %s' % name)
		if code == 100:
			return filter(None, out.strip().split(' '))[1]

	def install(self, name, version, *args):
		self.yum_command('install %s' %  self._join_packages_str('-', name, version, *args), raise_exc=True)


'''---------------------------------
# path to manifest
---------------------------------'''

class Manifest(object):
	_instance = None
	path = None

	def __init__(self, path=None):
		if not self.path:
			self.path = os.path.join(os.path.dirname(__file__), '../import.manifest')
			if not os.path.exists(self.path):
				self.path = None
				LOG.error('Import manifest not found')
				#TODO: realize finding manifest

		if path:
			if os.path.exists(path):
				self.path = path
			else:
				LOG.debug('Path `%s` not exist try standart path `%s`', path, self.path)

	def __new__(cls, *args, **kwargs):
		if not cls._instance:
			cls._instance = super(Manifest, cls).__new__(cls, *args, **kwargs)
		return cls._instance


def setup(path=None):
	Manifest(path)


class ImpLoader(object):
	'''Overloading find_modul and runtime install package if it not installed already'''

	def __init__(self, path=None):

		#available package managers
		self.pkg_mgrs = {'apt': AptPackageMgr,	'yum': YumPackageMgr}
		self.mgr = None
		self.path = None

		self.conf = configparser.ConfigParser()
		self.conf.read(Manifest().path)

		self.names =  ['apt' if disttool.is_debian_based() else 'yum']
		#['apt', 'apt:ubuntu', 'apt:ubuntu11', 'apt:ubuntu11.10']
		#['yum', 'yum:el', 'yum:el5', 'yum:centos5.7']
		dist = disttool.linux_dist()
		if disttool.is_redhat_based():
			self.names.append(self.names[0] + ':' + 'el')
			self.names.append(self.names[1] + dist[1].split('.')[0])
		else:
			self.names.append(self.names[0] + ':' + dist[0].lower())
			self.names.append(self.names[1] + dist[1].split('.')[0])
		self.names.append(self.names[0] + ':' + dist[0].lower() + dist[1])
		LOG.debug('names:`%s`', self.names)

	def _install_package(self, package):
		LOG.debug('install_package %s', package)
		
		dist_names = self.names
		while len(dist_names) > 0:
			dist_name = dist_names.pop()
			try:
				try:
					self.__installer(self.conf.get(dist_name, package))
					LOG.debug('Package `%s` successfully installed', package)
				except:
					raise Exception, 'Can`t install package `%s`. Details: %s' % (
						self.conf.get(dist_name, package), sys.exc_info()[1]), sys.exc_info()[2]
			except:
				LOG.debug('ImpLoader.install_package: %s', sys.exc_info()[1], 
					exc_info=sys.exc_info())

	def __installer(self, full_package_name):
		'''Try to install target package'''
		if disttool.is_debian_based():
			self.mgr = self.pkg_mgrs['apt']
		elif disttool.is_redhat_based():
			self.mgr = self.pkg_mgrs['yum']
		else:
			raise Exception('Operation system is unknown type. Can`t install package `%s`'
				' package manager is `undefined`' % full_package_name)
		version = self.mgr.candidates(full_package_name)[-1]
		self.mgr.install(full_package_name, version)

	'''-----------------------------
	# overloading find_modul
	-----------------------------'''

	def __find(self, full_name, path=None):
		subname = full_name.split(".")[-1]
		if subname != full_name and self.path is None:
			return None
		if self.path is None:
			path = None
		else:
			path = [os.path.realpath(self.path)]
		try:
			file, filename, etc = imp.find_module(subname, path)
		except ImportError:
			return None
		return file, filename, etc

	def find_module(self, full_name, path=None):
		# Note: we ignore 'path' argument since it is only used via meta_path
		LOG.debug('ImpLoader.find_modul. fullname=`%s`, path=`%s`', full_name, path or '')
		try:
			file, filename, etc = self.__find(full_name, path)
		except:
			LOG.debug('Modul `%s` is not found. Trying install it now...', full_name)
			try:
				self._install_package(full_name)
				file, filename, etc = self.__find(full_name, path)
			except:
				raise ImportError, 'Can`t install modul `%s`. Details: %s' % (full_name, 
					sys.exc_info()[1])

		return imp.load_module(full_name, file, filename, etc)

sys.meta_path = [ImpLoader()]


'''
#TODO: add configure in import this modul
def __import__(name, globals, locals=None, fromlist=None):
	# Fast path: see if the module has already been imported.

	try:
		return sys.modules[name]
	except KeyError:
		pass

	fp, pathname, description = imp.find_module(name)

	try:
		return imp.load_module(name, fp, pathname, description)
	finally:
		if fp:
			fp.close()'''

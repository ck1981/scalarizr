'''
Created on Jun 23, 2010

@author: marat
@author: Dmytro Korsakov
'''
from scalarizr.bus import bus
from scalarizr.behaviour import Behaviours
from scalarizr.handlers import Handler
from scalarizr.messaging import Messages
import logging
import os
from scalarizr.util import configtool, fstool
from xml.dom.minidom import parse


class StorageError(BaseException): pass

def get_handlers ():
	return [CassandraHandler()]

class CassandraHandler(Handler):
	_logger = None
	_queryenv = None
	_storage = None
	_storage_path = None
	_storage_conf = None

	def __init__(self):
		self._logger = logging.getLogger(__name__)
		self._queryenv = bus.queryenv_service
		bus.on("init", self.on_init)

	def on_init(self):
		bus.on("before_host_up", self.on_before_host_up)
		
	def accept(self, message, queue, behaviour=None, platform=None, os=None, dist=None):
		return Behaviours.CASSANDRA in behaviour and \
				(message.name == Messages.HOST_UP or message.name == Messages.HOST_DOWN)
		
	def on_before_host_up(self, message):
		config = bus.config
		role_name = config.get(configtool.SECT_GENERAL, configtool.OPT_ROLE_NAME)
		self._storage_path = config.get('behaviour_cassandra','storage_path')
		self._storage_conf = config.get('behaviour_cassandra','storage_conf')
		self.data_file_directory = self._storage_path + "/datafile" 
		self.commit_log_directory = self._storage_path + "/commitlog" 
		# Init storage
		role_params = self._queryenv.list_role_params(role_name)
		try:
			storage_name = role_params["cassandra_data_storage_engine"]
		except KeyError:
			storage_name = "eph"
				
		self._storage = StorageProvider().new_storage(storage_name)
		self._storage.init(self._storage_path)
		# Update CommitLogDirectory and DataFileDirectory in storage-conf.xml
		if not os.path.exists(self.data_file_directory):
			os.makedirs(self.data_file_directory) 
		if not os.path.exists(self.commit_log_directory):
			os.makedirs(self.commit_log_directory)
		
		xml = parse(self._storage_conf)
		data = xml.documentElement
		
		if len(data.childNodes):
			
			log_entry = data.getElementsByTagName("CommitLogDirectory")
			if log_entry:
				self._logger.debug("Rewriting CommitLogDirectory in cassandra config")
				log_entry[0].firstChild.nodeValue = self.commit_log_directory
			else:
				self._logger.debug("CommitLogDirectory not found in cassandra config")
			
			data_entry = data.getElementsByTagName("DataFileDirectory")
			if data_entry:
				self._logger.debug("Rewriting DataFileDirectory in cassandra config")
				data_entry[0].firstChild.nodeValue = self.data_file_directory
			else:
				self._logger.debug("DataFileDirectory not found in cassandra config")
				
			fw = open(self._storage_conf, 'w')
			fw.write(data.toxml())
			fw.close()
		
		# Update Seed configuration
		pass
	
	def on_HostUp(self, message):
		# Update Seed configuration
		pass
	
	def on_HostDown(self, message):
		# Update Seed configuration
		pass
	


class StorageProvider(object):
	
	_providers = None
	_instance = None
	
	def __new__(cls):
		if cls._instance is None:
			o = object.__new__(cls)
			o._providers = dict()
			cls._instance = o
		return cls._instance
	
	def new_storage(self, name, *args, **kwargs):
		if not name in self._providers:
			raise StorageError("Cannot create storage from undefined provider '%s'" % (name,))
		return self._providers[name](*args, **kwargs) 
	
	def register_storage(self, name, cls):
		if name in self._providers:
			raise StorageError("Storage provider '%s' already registered" % (name,))
		self._providers[name] = cls
		
	def unregister_storage(self, name):
		if not name in self._providers:
			raise StorageError("Storage provider '%s' is not registered" % (name,))
		del self._providers[name]
	
class Storage(object):
	def __init__(self):
		pass
	
	def init(self, mpoint, *args, **kwargs):
		pass
	
	def copy_data(self, src, *args, **kwargs):
		pass

class EbsStorage(Storage):
	pass

class EphemeralStorage(Storage):
	_platform = None
	def __init__(self):
		self._platform = bus.platform
		self._logger = logging.getLogger(__name__)
		
	def init(self, mpoint, *args, **kwargs):
		devname = '/dev/' + self._platform.get_block_device_mapping()["ephemeral0"]
		
		try:
			self._logger.debug("Trying to mount device %s and add it to fstab", devname)
			fstool.mount(device = devname, mpoint = mpoint, options = ["-t auto"], auto_mount = True)
		except fstool.FstoolError, e:
			if fstool.FstoolError.NO_FS == e.code:
				self._logger.debug("Trying to create file system on device %s, mount it and add to fstab", devname)
				fstool.mount(device = devname, mpoint = mpoint, options = ["-t auto"], make_fs = True, auto_mount = True)
			else:
				raise
	
	def copy_data(self, src, *args, **kwargs):
		pass
	
StorageProvider().register_storage("eph", EphemeralStorage)	


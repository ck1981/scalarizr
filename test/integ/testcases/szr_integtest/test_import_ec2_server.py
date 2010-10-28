'''
Created on Oct 2, 2010

@author: spike
'''

import unittest

from szr_integtest_libs.scalrctl import import_server, ScalrConsts, ScalrCtl
from szr_integtest import get_selenium, Ec2TestAmis, config, MutableLogFile
import socket
from boto.ec2.connection import EC2Connection
from scalarizr.libs.metaconf import NoPathError
import time
from szr_integtest_libs import SshManager, exec_command
from szr_integtest_libs.szrdeploy import ScalarizrDeploy
import logging
from optparse import OptionParser
import sys
import re

SECURITY_GROUP = 'webta.scalarizr'

class ImportEc2Server:
	ami        = None	
	ip_address = None
	ec2 	   = None
	instance   = None
	
	def __init__(self, sysargs):
		self._logger = logging.getLogger(__name__)
		self.passed = True
		self.sys_args = sysargs
		self.scalr_ctl = ScalrCtl()

	def _install_software(self, channel, distr):
		self._logger.info("Installing screen")
		if distr == 'debian':
			out = exec_command(channel, 'apt-get -y install screen', 240)
			error = re.search('^E:\s*(?P<err_text>.+?)$', out, re.M)
			if error:
				raise Exception("Can't install screen package: '%s'" % error.group('err_text'))		

		else:
			out = exec_command(channel, 'yum -y install screen', 240)
			if not re.search('Complete!|Nothing to do', out):
				raise Exception('Cannot install screen %s' % out)
			exec_command(channel, 'chmod 777 /var/run/screen')

	def _change_behaviour(self, import_server_str):
		return import_server_str
	
	def _import_server(self, role_name):
		return import_server(get_selenium(), ScalrConsts.Platforms.PLATFORM_EC2 ,\
			ScalrConsts.Behaviours.BEHAVIOUR_BASE , self.ip_address, role_name)
		
	def _get_role_name(self):
		return 'Test_base_%s' % time.strftime('%Y_%m_%d_%H%M')
	
	def _avoid_updates(self, channel, distr):
		self._logger.info('Turning off updates from SVN.')
		debian = distr is 'debian'
		rm_updates_str = 'update-rc.d scalarizr_update remove' if debian else '/sbin/chkconfig --del scalarizr_update'
		exec_command(channel, rm_updates_str)
	
	
	def cleanup(self):
		if not self.sys_args.no_cleanup:
			if self.instance:

				self._logger.info('Terminating instance %s ' % str(self.instance.id))
				self.ec2.terminate_instances([str(self.instance.id)])
			
			if self.ami and self.ec2:
				image = self.ec2.get_image(self.ami)
				snap_id = image.block_device_mapping['/dev/sda1'].snapshot_id
				self.ec2.deregister_image(self.ami)
				self.ec2.delete_snapshot(snap_id)
				#TODO: Clean scalr's database 

	def test_import(self):
		
		try:
			ec2_key_id = config.get('./boto-ec2/ec2_key_id')
			ec2_key    = config.get('./boto-ec2/ec2_key')
			key_name   = config.get('./boto-ec2/key_name')
			key_path   = config.get('./boto-ec2/key_path')
			key_password   = config.get('./boto-ec2/ssh_key_password')
		except NoPathError:
			raise Exception("Configuration file doesn't contain ec2 credentials")
		
		self.ec2 = EC2Connection(ec2_key_id, ec2_key)

		if not self.sys_args.inst_id:
			reservation = self.ec2.run_instances(self.sys_args.ami, security_groups = [SECURITY_GROUP], instance_type='t1.micro', placement = 'us-east-1a', key_name = key_name)
			self.instance = reservation.instances[0]
			self._logger.info('Started instance %s', self.instance.id)
			while not self.instance.state == 'running':
				self.instance.update()
				time.sleep(5)
			self._logger.info("Instance's %s state is 'running'" , self.instance.id)
		else:
			try:
				reservation = self.ec2.get_all_instances(self.sys_args.inst_id)[0]
			except:
				raise Exception('Instance %s does not exist' % self.sys_args.inst_id)
	
			self.instance = reservation.instances[0]
		
		self.root_device = self.instance.rootDeviceType

		self.ip_address = socket.gethostbyname(self.instance.public_dns_name)

		sshmanager = SshManager(self.ip_address, key_path, key_pass = key_password)
		sshmanager.connect()

		deployer = ScalarizrDeploy(sshmanager)
		distr = deployer.distr
		
		# TODO: add nightly-build support
		self._logger.info("Adding repository")
		deployer.add_repos('release')

		self._logger.info("Installing package")

		deployer.install_package()

		self._logger.info("Apply changes from dev branch (tarball)")

		deployer.apply_changes_from_tarball()
		
		role_name = self._get_role_name()
		self._logger.info("Role name: %s", role_name)
		self._logger.info("Importing server in scalr's interface")	#import sys;sys.argv = ['', 'Test.test_ ']
		import_server_str = self._import_server(role_name)
		
		#temporary!
		import_server_str = self._change_behaviour(import_server_str)

		#import_server_str += ' &'
		channel = sshmanager.get_root_ssh_channel()
		
		self._logger.info("Hacking configuration files")
		exec_command(channel, 'mv /etc/scalr/logging-debug.ini /etc/scalr/logging.ini')
		exec_command(channel, "sed -i 's/consumer_url = http:\/\/localhost/consumer_url = http:\/\/0.0.0.0/g' /etc/scalr/public.d/config.ini")

		if self.sys_args.no_updates:
			self._avoid_updates(channel, distr)
		
		self._install_software(channel, distr)
		
		self._logger.info("Running scalarizr with import options")
		exec_command(channel, 'screen -md %s' % import_server_str)
		# Sleep for a while for scalarizr initializing
		time.sleep(2)
		#tail_log_channel(channel)
		log = MutableLogFile(channel)
		reader = log.head()
		self._logger.info("Waiting for hello message delivered")
		# RegExp   																		# Timeout
		
		reader.expect("Message 'Hello' delivered", 										15)
		
		self._logger.info("Hello delivered")
		self.scalr_ctl.exec_cronjob('ScalarizrMessaging')
		
		if self.root_device == 'instance-store':
			reader.expect("Make image .+ from volume /",			 					240)
		else:
			reader.expect("Make EBS volume /dev/sd.+ from volume /", 					240)

		reader.expect("Volume bundle complete!", 										2400)
		self._logger.info("Volume with / bundled")
		

		if self.root_device == 'instance-store':
			reader.expect( 'Bundling image...',								    	    240)
			reader.expect( 'Encrypting image',											240)
			reader.expect( 'Splitting image into chunks',								240)
			reader.expect( 'Encrypting keys',											240)
			reader.expect( 'Image bundle complete!',									240)
			
			self._logger.info("Image bundled!")
			
			reader.expect( 'Uploading bundle',											240)
			reader.expect( 'Enqueue files to upload',									240)
			reader.expect( 'Uploading files',											240)
			reader.expect( 'Registration complete!',									240)
			self.ami = reader.expect("Image (?P<ami_id>ami-\w+) available", 			360).group('ami_id')

		else:
			reader.expect( "Creating snapshot of root device image", 					240)
			self._logger.info("Creating snapshot of root device image")
			reader.expect( "Checking that snapshot (?P<snap_id>snap-\w+) is completed",240)
			self._logger.info("Checking that snapshot is completed")
			reader.expect( "Snapshot snap-\w+ completed", 								420)
			self._logger.info("Snapshot completed")
			reader.expect( "Registering image", 										120)
		
			self.ami = reader.expect("Checking that (?P<ami_id>ami-\w+) is available",  120).group('ami_id')

			self._logger.info("Checking for %s completed", self.ami)
			reader.expect( "Image (?P<ami>ami-\w+) available", 						    360)
		
			self._logger.info("Ami created: %s", self.ami)
		
			reader.expect( "Image registered and available for use", 					240)
			
		reader.expect( "Rebundle complete!", 											240)
		self._logger.info("Rebundle complete!")
		log.detach(reader.queue)
		del(reader)
		self.scalr_ctl.exec_cronjob('ScalarizrMessaging')
		self.scalr_ctl.exec_cronjob('BundleTasksManager')
		self.scalr_ctl.exec_cronjob('BundleTasksManager')
		
		#exec_command(channel,)
		# TODO: run <import_server_str> on instance, read log while bundle not complete, return ami id . 
		# Don't forget to run crons!

def _parse_args():
	parser = OptionParser()
	parser.add_option('-c', '--no-cleanup', dest='no_cleanup', action='store_true', default=False, help='Do not cleanup test data')
	parser.add_option('-m', '--ami', dest='ami', default=Ec2TestAmis.UBUNTU_1004_EBS, help='Amazon AMI')
	parser.add_option('-i', '--instance-id', dest='inst_id', default=None, help='Running instance')
	parser.add_option('-n', '--no-updates', dest='no_updates', action='store_true', default=False, help='Turn off updates from SVN')
	
	parser.parse_args()
	return parser.values


class TestImportEc2Server(unittest.TestCase):
	
	importer = None
	
	def setUp(self):
		self.importer = ImportEc2Server(sysargs)

	def test_import(self):
		self.importer.test_import()

	def tearDown(self):
		pass
		#self.importer.cleanup()
	
			
if __name__ == "__main__":
	sysargs = _parse_args()
	del sys.argv[1:]
	unittest.main()
	
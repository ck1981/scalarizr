'''
Created on Mar 2, 2010

@author: marat
'''
from scalarizr.bus import bus
from scalarizr.handlers import Handler
from scalarizr.util import system
import logging



def get_handlers ():
	return [Ec2LifeCircleHandler()]

class Ec2LifeCircleHandler(Handler):
	_logger = None
	_platform = None
	"""
	@ivar scalarizr.platform.ec2.Ec2Platform:
	"""
	
	def __init__(self):
		self._logger = logging.getLogger(__name__)
		self._platform = bus.platform
		bus.on("init", self.on_init)		
	
	def on_init(self, *args, **kwargs):
		bus.on("before_hello", self.on_before_hello)		
		bus.on("before_host_init", self.on_before_host_init)
		bus.on("before_restart", self.on_before_restart)

		msg_service = bus.messaging_service
		producer = msg_service.get_producer()
		producer.on("before_send", self.on_before_message_send)
		
		# Set the hostname to this instance's public hostname
		system("hostname " + self._platform.get_public_hostname())
	
	
	def on_before_hello(self, message):
		"""
		@param message: Hello message
		"""
		
		message.aws_instance_id = self._platform.get_instance_id()
		message.aws_instance_type = self._platform.get_instance_type()		
		message.aws_ami_id = self._platform.get_ami_id()
		message.aws_avail_zone = self._platform.get_avail_zone()


	def on_before_host_init(self, message):
		"""
		@param message: HostInit message
		"""

		message.ssh_pub_key = self._platform.get_ssh_pub_key()

	def on_before_restart(self, message):
		"""
		@param message: Restart message
		@type message: scalarizr.messaging.Message 
		"""
		
		"""
		@todo Update ips, reset platform meta-data
 		@see http://docs.amazonwebservices.com/AWSEC2/latest/DeveloperGuide/index.html?Concepts_BootFromEBS.html#Stop_Start
		"""
		pass

	def on_before_message_send(self, queue, message):
		"""
		@todo: add aws specific here
		"""
		
		pass
	

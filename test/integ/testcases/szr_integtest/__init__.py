from ConfigParser import ConfigParser
from selenium import selenium
from multiprocessing import Process
from scalarizr.libs.metaconf import NoPathError, Configuration
import time
import os
import signal
import sys
import paramiko

BASE_PATH = os.path.join(os.path.dirname(__file__), '..' + os.path.sep + '..')
RESOURCE_PATH = os.path.join(BASE_PATH, 'resources')
OPT_SESSION_ID = 'session_id'

config = Configuration('ini')
user_config = Configuration('ini')

config.read(os.path.join(RESOURCE_PATH, 'integ_test.ini'))

_user_ini_path = os.path.expanduser('~/.scalr-dev/integ_test.ini')

if os.path.exists(_user_ini_path):
	config.read(_user_ini_path)
	user_config.read(_user_ini_path)
else:
	basepath = os.path.dirname(_user_ini_path)
	if not os.path.isdir(basepath):
		os.makedirs(basepath)
	user_config.add('./general')


_sel_started = False

try:
	_sel = selenium(
			config.get('./general/selenium_rc_host'), 
			config.get('./general/selenium_rc_port'), 
			'*firefox', 
			config.get('./general/scalr_net_url')
			)
	
except NoPathError:
	raise Exception("Configuration file doesn't contain all essential options")

		
def check_windows(_sel):
	try:
		_sel.get_all_window_titles()
	except:
		sys.exit(-1)	

def get_selenium():
	try:
		session_id = config.get('./general/' + OPT_SESSION_ID)
		try:
			_sel.sessionId = unicode(session_id)
			t = Process(target=check_windows, args=(_sel,))
			t.start()
			start_time = time.time()
			while (time.time() - start_time < 3):
				time.sleep(0.1)
				if not t.is_alive():
					if t.exitcode == 0:
						break
					else:
						raise BaseException()
			else:
				os.kill(t.pid, signal.SIGKILL)
				raise BaseException('timeout')
			_sel.delete_all_visible_cookies()
			globals()['_sel_started'] = True
		except:
			_sel.stop()
			globals()['_sel_started'] = False
	except:
		globals()['_sel_started'] = False
			
	if not _sel_started:
		try:
			_sel.start()
		except (Exception, BaseException), e:
			raise Exception("Can't connect to selenium RC or start a session: %s" % e)
		globals()['_sel_started'] = True
		
		config.set('./general/' + OPT_SESSION_ID, _sel.sessionId)
		user_config.set('./general/' + OPT_SESSION_ID, _sel.sessionId, force = True)
		user_config.write(open(_user_ini_path, 'w'))
	return _sel
		
	
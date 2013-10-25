
from scalarizr.api import operation

import mock
import time
import threading
from nose.tools import eq_, ok_

import pprint


@mock.patch.dict('scalarizr.node.__node__', {
	'messaging': mock.MagicMock()
})
class TestOperation(object):

	def assert_op_result(self, status=None):
		from scalarizr.node import __node__
		args, kwds = __node__['messaging'].send.call_args 
		eq_(args[0], 'OperationResult')
		if status:
			eq_(kwds['body']['status'], status)
		return kwds['body']


	def test_result_error(self):
		def fn_raises_error(op):
			def deep():
				op.logger.info('Im deep and going deeper')
				deeper()
			def deeper():
				op.logger.info('Im in deep and continue')
				abyss()
			def abyss():
				op.logger.info('Im in abyss and trying to open file')
				open('/non/existed/file')
			deep()

		op = operation.OperationAPI().create('test_result_error', fn_raises_error)
		op.run()

		result = self.assert_op_result('failed')
		eq_(result['name'], 'test_result_error')
		eq_(len(result['logs']), 4)
		ok_(result['logs'][-1].endswith('Reason: [Errno 2] No such file or directory: \'/non/existed/file\''))
		ok_('deep()' in result['trace'])
		ok_('deeper()' in result['trace'])
		ok_('abyss()' in result['trace'])


	def test_result(self):
		result_data = {
			'embed': 'data'
		}

		def fn(op):
			return result_data
		op = operation.OperationAPI().create('test_result', fn)
		op.run()

		result = self.assert_op_result('completed')
		eq_(result['result'], result_data)


	def check_cancel(self, asserts=None, op_func=None, cancel_func=None):
		started = threading.Event()
		canceled = threading.Event()

		def fn(op):
			ok_(not op.canceled)
			started.set()
			canceled.wait()
			ok_(op.canceled)
			if op_func:
				op_func()

		op = operation.OperationAPI().create('test_cancel', fn, cancel_func=cancel_func)
		op.run_async()
		started.wait()
		op.cancel()
		canceled.set()
		time.sleep(.2) # Interrupt thread
		if asserts:
			asserts()

	def test_cancel_error(self):
		msg = 'Exception in op function after it was canceled'

		def asserts():
			result = self.assert_op_result('canceled')
			eq_(msg, result['error'])
			
		self.check_cancel(asserts, op_func=mock.Mock(side_effect=Exception(msg)))


	def test_cancel(self):
		def asserts():
			result = self.assert_op_result('canceled')
			ok_(result['error'].startswith('User canceled'))

		self.check_cancel(asserts)

	def test_cancel_func(self):
		cancel_func = mock.Mock()
		def asserts():
			eq_(cancel_func.call_count, 1)
		self.check_cancel(asserts, cancel_func=cancel_func)

	def test_cancel_func_error(self):
		self.check_cancel(cancel_func=mock.Mock(side_effect=Exception))





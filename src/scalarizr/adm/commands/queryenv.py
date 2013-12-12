import inspect

from scalarizr.util import system2
from scalarizr.adm.command import Command
from scalarizr.adm.command import get_section
from scalarizr.adm.command import TAB_SIZE


def transpose(l):
    return map(list, zip(*l))


class Queryenv(Command):
    """
    queryenv command is used to launch queryenv methods.

    Usage:
      queryenv get-https-certificate
      queryenv get-latest-version
      queryenv list-ebs-mountpoints
      queryenv list-roles [--behaviour=<bhvr>] [--role-name=<rolename>] [--with-initializing]
      queryenv list-virtualhosts [--name=<name>] [--https]
    
    Options for list-roles:
      -b, --behaviour=<bhvr>      Role behaviour.
      -r, --role-name=<rolename>  Role name.
      -i, --with-initializing     Show initializing servers

    Options for list-virtualhosts:
      -n, --name               Show virtual host by name
      -s, --https              Show virtual hosts by https
    """

    def __init__(self):
        super(Queryenv, self).__init__()

    def help(self):
        doc = super(Queryenv, self).help()
        methods = [(' '*TAB_SIZE*3) + m for m in self.supported_methods()]
        return doc + (' '*TAB_SIZE*2) + '\nSupported methods:\n' + '\n'.join(methods)

    def supported_methods(self):
        usage_section = get_section(self.__doc__)
        usages = re.findall(r'queryenv .+?\s', usage_section)
        methods = [s.split()[1] for s in usages]
        return methods

    @classmethod
    def queryenv(cls):
        if not hasattr(cls, '_queryenv'):
            cls._queryenv = new_queryenv()
        return cls._queryenv

    def _display_get_https_certificate(self, out):
        headers = ['cert', 'pkey', 'cacert']
        print make_table(out, headers)

    def _display_get_latest_version(self, out):
        print make_table([out], ['version'])

    def _display_list_ebs_mountpoints(self, out):
        headers = ['name', 'dir', 'createfs', 'isarray', 'volume-id', 'device']
        table_data = []
        for d in out:
            volume_params = [(v.volume_id, v.device) for v in d.volumes]
            volumes, devices = transpose(volume_params)
            table_data.append([d.name, d.dir, d.create_fs, d.is_array, volumes, devices])
        print make_table(table_data, headers)

    def _display_list_roles(self, out):
        headers = ['behaviour',
                   'name',
                   'farm-role-id',
                   'index',
                   'internal-ip',
                   'external-ip',
                   'replication-master']
        table_data = []
        for d in out:
            behaviour = ', '.join(d.behaviour)
            for host in d.hosts:
                table_data.append([behaviour, 
                                   d.name,
                                   d.farm_role_id,
                                   str(host.index),
                                   host.internal_ip,
                                   host.external_ip,
                                   str(host.replication_master)])
        print make_table(table_data, headers)

    def _display_list_virtualhosts(self, out):
        headers = ['hostname', 'https', 'type', 'raw']
        table_data = [[d.hostname, d.https, d.type, d.raw] for d in out]
        print make_table(table_data, headers)

    def _display_out(self, method, out):
        all_display_methods = [m for m in dir(self) if m.startswith('_display')]
        display_method = None
        for m in all_display_methods:
            if method.replace('-', '_') in m:
                display_method = getattr(self, m)
                break

        if display_method:
            display_method(self, out)
        elif isinstance(out, list) and isinstance(out[0], list):
            print make_table(out)
        else:
            print out


    def _run_queryenv_method(self, method, kwds, kwds_mapping=None):
        """
        Executes queryenv method with given `kwds`. `kwds` can contain excessive
        key-value pairs, this method filters it and passes only acceptable
        kwds by target method. `kwds_mapping` defines how `kwds` keys will be
        renamed when passed to queryenv method.
        """
        if not method:
            return
        if not kwds_mapping:
            kwds_mapping = {}

        method = method.replace('-', '_')
        m = getattr(self.queryenv, method)
        argnames = inspect.getargspec(m).args
        filtered_kwds = {}
        for k, v in kwds.items():
            arg_name = kwds_mapping.get(k, k)
            if arg_name in argnames:
                filtered_kwds[arg_name] = v

        self._display_method_out(method, m(**filtered_kwds))

    def __call__(self, **kwds):
        method = None
        supported_methods = self.supported_methods()
        for kwd in kwds.keys():
            if kwd in supported_methods:
                method = kwd
                kwds.pop(kwd)
                break

        if not method:
            # if supported method was not found in kwds, assuming that kwds
            # contains only one element which is method name so we can call it
            # without parameters
            method = kwds.keys()[0]

        if method == 'list-roles':
            out = self._run_queryenv_method(
                method,
                kwds,
                {'with_initializing': 'with_init'})
        else:
            out = self._run_queryenv_method(method, kwds)
        self._display_out(method, out)


commands = [Queryenv]

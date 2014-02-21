from __future__ import with_statement
'''
Created on Oct 12, 2010

@author: marat
'''

from scalarizr.bus import bus
from scalarizr.handlers import rebundle as rebundle_hdlr
from scalarizr.handlers.ec2 import rebundle as ec2_rebundle_hdlr
from scalarizr.handlers import HandlerError
from scalarizr import linux

import os
import glob
from subprocess import Popen, PIPE
from binascii import hexlify, unhexlify
from hashlib import sha1

from M2Crypto import BN, EVP, RSA, X509

IMAGE_IO_CHUNK = 10 * 1024
IMAGE_SPLIT_CHUNK = IMAGE_IO_CHUNK * 1024
LOG = ec2_rebundle_hdlr.LOG

def get_handlers ():
    return [EucaRebundleHandler()]


class EucaRebundleStrategy(ec2_rebundle_hdlr.RebundleInstanceStoreStrategy):
    def run(self):
        if not linux.which('euca-bundle-vol'):
            raise HandlerError('euca-bundle-vol command not found, please install "euca2ools" package')

        cert_path = pk_path = cloud_cert_path = fstab_path = None
        try:
            cert, pk = self._platform.get_cert_pk()
            cert_path = bus.cnf.write_key('euca-cert.pem', cert)
            pk_path = bus.cnf.write_key('euca-pk.pem', pk)
            cloud_cert_path = bus.cnf.write_key('euca-cloud-cert.pem', self._platform.get_ec2_cert())
            access_key, secret_key = self._platform.get_access_keys()

            environ = os.environ.copy()
            environ.update({
                'EUCALYPTUS_CERT': cloud_cert_path,
                'EC2_CERT': cert_path,
                'EC2_PRIVATE_KEY': pk_path,
                'EC2_USER_ID': self._platform.get_account_id(),
                'EC2_ACCESS_KEY': access_key,
                'EC2_SECRET_KEY': secret_key,
                'EC2_URL': self._platform.get_access_data('ec2_url'),
                'S3_URL': self._platform.get_access_data('s3_url')
            })

            with open('/etc/fstab') as fp:
                fstab_path = bus.cnf.write_key('euca-fstab', fp.read())
            self._fix_fstab(filename=fstab_path)

            # Create image object for gathering directories exclude list
            image = rebundle_hdlr.LinuxImage('/', 
                        os.path.join(self._destination, self._image_name), 
                        self._excludes)

            LOG.info('Executing euca-bundle-vol')
            out = linux.system((
                    linux.which('euca-bundle-vol'), 
                    '--arch', linux.os['arch'],
                    '--size', str(self._image_size),
                    '--destination', self._destination,
                    '--exclude', ','.join(image.excludes),
                    '--fstab', fstab_path,
                    '--prefix', self._image_name,
                    '--volume', '/'
                ),
                env=environ
            )[0]
            LOG.info(out)

            LOG.info('Uploading image')
            files_prefix = os.path.join(self._destination, self._image_name)
            files = glob.glob(files_prefix + '*')
            s3_manifest_path = self._upload_image_files(files, files_prefix + '.manifest.xml')

            return self._register_image(s3_manifest_path)

        finally:
            linux.system('chmod 755 {0}/keys/euca-*'.format(bus.cnf.private_path()), shell=True)
            linux.system('rm -f {0}/keys/euca-*'.format(bus.cnf.private_path()), shell=True)
            linux.system('rm -f {0}/{1}.*'.format(self._destination, self._image_name), shell=True)


class EucaRebundleHandler(ec2_rebundle_hdlr.Ec2RebundleHandler):
    def __init__(self):
        ec2_rebundle_hdlr.Ec2RebundleHandler.__init__(self, instance_store_strategy_cls=EucaRebundleStrategy)

    @property
    def _s3_bucket_name(self):
        pl = bus.platform
        return 'scalr2-images-%s' % pl.get_account_id()


class EucaRebundleInstanceStoreStrategy(ec2_rebundle_hdlr.RebundleInstanceStoreStrategy):
    def _bundle_image(self, name, image_file, user, destination, user_private_key_string,
                                    user_cert_string, ec2_cert_string, key=None, iv=None):
        LOG.info("Bundling image...")
        Popen(['sync']).communicate()

        image_size, sha_image_digest = self.check_image(image_file, destination)
        tgz_file = self.tarzip_image(name, image_file, destination)
        encrypted_file, key, iv, bundled_size = self.encrypt_image(tgz_file)
        os.remove(tgz_file)
        parts, parts_digest = self.split_image(encrypted_file)

        # Create bundle manifest
        user_public_key = X509.load_cert_string(user_cert_string).get_pubkey().get_rsa()
        user_private_key = RSA.load_key_string(user_private_key_string)
        ec2_public_key = X509.load_cert_string(ec2_cert_string).get_pubkey().get_rsa()

        pad = RSA.pkcs1_padding
        user_encrypted_key = hexlify(user_public_key.public_encrypt(key, pad))
        user_encrypted_iv = hexlify(user_private_key.public_encrypt(iv, pad))
        ec2_encrypted_key = hexlify(ec2_public_key.public_encrypt(key, pad))
        ec2_encrypted_iv = hexlify(ec2_public_key.public_encrypt(iv, pad))

        manifest_file = os.path.join(destination, name + '.manifest.xml')
        manifest = ec2_rebundle_hdlr.AmiManifest(
                name=name,
                user=user,
                arch=self._get_arch(),
                parts=zip(map(lambda x: os.path.basename(x), parts), parts_digest),
                image_size=image_size,
                bundled_size=bundled_size,
                user_encrypted_key=user_encrypted_key,
                ec2_encrypted_key=ec2_encrypted_key,
                user_encrypted_iv=user_encrypted_iv,
                ec2_encrypted_iv=ec2_encrypted_iv,
                image_digest=sha_image_digest,
                user_private_key=user_private_key,
                kernel_id=self._platform.get_kernel_id(),
                ramdisk_id=self._platform.get_ramdisk_id(),
                ancestor_ami_ids=self._platform.get_ancestor_ami_ids(),
                block_device_mapping=self._platform.block_devs_mapping()
        )
        manifest.save(manifest_file)

        LOG.info("Image bundle complete!")
        return manifest_file, manifest

    def check_image(self, image_file, path):
        LOG.info('Checking image')
        if not os.path.exists(path):
            os.makedirs(path)
        image_size = os.path.getsize(image_file)
        LOG.debug('Image size: %d bytes', image_size)

        # Euca2ool 1.3 main-31337 2009-04-04
        # Buggy calculates signature from empty string
        #return (image_size, 'da39a3ee5e6b4b0d3255bfef95601890afd80709')

        in_file = open(image_file, 'rb')
        sha_image = sha1()
        while 1:
            buf = in_file.read(IMAGE_IO_CHUNK)
            if not buf:
                break
            sha_image.update(buf)
        return (image_size, hexlify(sha_image.digest()))


    def tarzip_image(self, prefix, file, path):
        LOG.info('Tarring image')

        tar_file = '%s.tar.gz' % os.path.join(path, prefix)
        outfile = open(tar_file, 'wb')
        file_path = self.get_file_path(file)
        tar_cmd = ['tar', 'c', '-S']
        if file_path:
            tar_cmd.append('-C')
            tar_cmd.append(file_path)
            tar_cmd.append(self.get_relative_filename(file))
        else:
            tar_cmd.append(file)
        p1 = Popen(tar_cmd, stdout=PIPE)
        p2 = Popen(['gzip'], stdin=p1.stdout, stdout=outfile)
        p2.communicate()
        outfile.close
        if os.path.getsize(tar_file) <= 0:
            raise HandlerError('Could not tar image')

        return tar_file


    def encrypt_image(self, file):
        LOG.info('Encrypting image')
        enc_file = '%s.part' % file.replace('.tar.gz', '')

        key = hex(BN.rand(16 * 8))[2:34].replace('L', 'c')
        iv = hex(BN.rand(16 * 8))[2:34].replace('L', 'c')
        LOG.debug('Key: %s', key)
        LOG.debug('IV: %s', iv)

        k = EVP.Cipher(alg='aes_128_cbc', key=unhexlify(key),
                                   iv=unhexlify(iv), op=1)

        in_file = open(file)
        out_file = open(enc_file, 'wb')
        self.crypt_file(k, in_file, out_file)
        in_file.close()
        out_file.close()
        bundled_size = os.path.getsize(enc_file)
        return (enc_file, key, iv, bundled_size)


    def crypt_file(self, cipher, in_file, out_file):
        while 1:
            buf = in_file.read(IMAGE_IO_CHUNK)
            if not buf:
                break
            out_file.write(cipher.update(buf))
        out_file.write(cipher.final())

    def split_image(self, file):
        LOG.info('Splitting image...')
        return self.split_file(file, IMAGE_SPLIT_CHUNK)

    def split_file(self, file, chunk_size):
        parts = []
        parts_digest = []
        file_size = os.path.getsize(file)
        in_file = open(file, 'rb')
        number_parts = int(file_size / chunk_size)
        number_parts += 1
        bytes_read = 0
        for i in range(0, number_parts, 1):
            filename = '%s.%d' % (file, i)
            part_digest = sha1()
            file_part = open(filename, 'wb')
            LOG.debug('Part: %s', self.get_relative_filename(filename))
            part_bytes_written = 0
            while part_bytes_written < IMAGE_SPLIT_CHUNK:
                data = in_file.read(IMAGE_IO_CHUNK)
                file_part.write(data)
                part_digest.update(data)
                data_len = len(data)
                part_bytes_written += data_len
                bytes_read += data_len
                if bytes_read >= file_size:
                    break
            file_part.close()
            parts.append(filename)
            parts_digest.append(hexlify(part_digest.digest()))

        in_file.close()
        return (parts, parts_digest)

    def get_relative_filename(self, filename):
        f_parts = filename.split('/')
        return f_parts[len(f_parts) - 1]

    def get_file_path(self, filename):
        file_path = os.path.dirname(filename)
        if len(file_path) == 0:
            file_path = '.'
        return file_path

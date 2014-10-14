#!/usr/bin/env python

import argparse
import boto
from boto.s3.key import Key
import tarfile
import tempfile
import glob
import os
import requests
import json
import ConfigParser
import re

class PulpTar(object):
    """Models tarfile exported from Pulp"""
    def __init__(self, tarfile):
        self.tarfile = tarfile
        self.tar_tempdir = tempfile.mkdtemp()

    @property
    def crane_metadata_file(self):
        """Full path to crane metadata file"""
        json_files = glob.glob(self.tar_tempdir + "/*.json")
        if len(json_files) == 1:
            return json_files[0]
        else:
            print "More than one metadata file found"
            exit(1)

    @property
    def docker_images_dir(self):
        """Temp dir of docker images"""
        return self.tar_tempdir + "/web"

    def extract_tar(self):
        """Extract tarfile into temp dir"""
        tar = tarfile.open(self.tarfile)
        tar.extractall(path=self.tar_tempdir)
        print "Extracted tarfile to %s" % self.tar_tempdir
        print self.crane_metadata_file
        tar.close()

class AwsS3(object):
    """Interactions with AWS S3"""
    def __init__(self, bucket, app, images_dir, mask_layers):
        self.bucket = bucket
        self.app = app
        self.images_dir = images_dir
        self.mask_layers = mask_layers

    def upload_layers(self, files):
        """Upload image layers to S3 bucket"""
        s3 = boto.connect_s3()
        bucket = s3.create_bucket(self.bucket)
        for f, path in files:
            with open(f, 'rb') as f:
                dest = os.path.join((self.app), path)
                key = Key(bucket=bucket, name=dest)
                key.set_contents_from_file(f, replace=True)
                key.set_acl('public-read')
                print 'Successfully uploaded to %s:%s' % (bucket, dest)

    def walk_dir(self, layer_dir):
        """Walk image directory, returns list of tuples"""
        files = []
        if os.path.isdir(layer_dir):
            # Walk the directory to get all the files to be uploaded
            for dirpath, dirnames, filenames in os.walk(layer_dir):
                for filename in filenames:
                    layer_id = dirpath.split('/')
                    if layer_id[-1] in self.mask_layers:
                        print "Skipping layer %s" % layer_id[-1]
                        continue
                    filename = os.path.join(dirpath, filename)
                    files.append((filename, os.path.relpath(filename, layer_dir)))
        else:
            assert os.path.exists(layer_dir), '%s does not exist' % layer_dir
            files.append((layer_dir, os.path.basename(layer_dir)))
        return files

class Openshift(object):
    def __init__(self):
        self.os_url = "https://openshift.com"

    def connect(self):
        data = json.dumps({'name':'test', 'description':'some test repo'})
        r = requests.post(self.os_url, data, auth=('user', '*****'))
        print r.json

def main():
    """Entrypoint for script"""

    parser = argparse.ArgumentParser()
    parser.add_argument('bucket_name',
                       metavar='MY_BUCKET_NAME',
                       help='Name of the AWS S3 bucket, i.e. isv.images')
    parser.add_argument('app_name',
                       metavar='APPLICATION_NAME',
                       help='Name of the application being uploaded, i.e. myapp')
    parser.add_argument('tarfile',
                       metavar='MYAPP.TAR',
                       help='Name of the tarfile being uploaded')


    args = parser.parse_args()

    config = ConfigParser.ConfigParser()
    config.read('raas.cfg')
    mask_layers = re.split(',| ', config.get('redhat', 'mask_layers'))
    pulp = PulpTar(args.tarfile)
    pulp.extract_tar()
    s3 = AwsS3(args.bucket_name, args.app_name, pulp.docker_images_dir, mask_layers)
    files = s3.walk_dir(pulp.docker_images_dir)
    s3.upload_layers(files)

    #os = Openshift()
    #os.connect()


if __name__ == '__main__':
    main()


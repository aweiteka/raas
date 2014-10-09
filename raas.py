#!/usr/bin/env python

import argparse
import boto
from boto.s3.key import Key
import tarfile
import tempfile
import glob

class PulpTar(object):
    def __init__(self, tarfile):
        self.tarfile = tarfile
        self.tar_tempdir = tempfile.mkdtemp()

    @property
    def crane_metadata_file(self):
        json_files = glob.glob(self.tar_tempdir + "/*.json")
        if len(json_files) == 1:
            return json_files[0]
        else:
            print "More than one metadata file found"
            exit(1)

    @property
    def docker_images_dir(self):
        return self.tar_tempdir + "/web"

    def extract_tar(self):
        tar = tarfile.open(self.tarfile)
        tar.extractall(path=self.tar_tempdir)
        print "Extracted tarfile to %s" % self.tar_tempdir
        print self.crane_metadata_file
        tar.close()

class AwsS3(object):
    def __init__(self, bucket, app, images_dir):
        self.bucket = bucket
        self.app = app
        self.images_dir = images_dir

    @property
    def key(self):
        return self.app + "/" + self.images_dir

    def upload_tar(self):
        s3 = boto.connect_s3()
        bucket = s3.create_bucket(self.bucket)
        k = Key(bucket)
        k.key = self.key
        k.set_contents_from_filename(self.images_dir)
        k.set_acl('public-read')
        for key in bucket.list():
            print key.name

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

    pulp = PulpTar(args.tarfile)
    pulp.extract_tar()
    #s3 = AwsS3(args.bucket_name, args.app_name, pulp.docker_images_dir)
    #s3.upload_tar()


if __name__ == '__main__':
    main()


#!/usr/bin/env python

import argparse
import boto
from boto.s3.key import Key

class PulpTar(object):
    def __init__(self, tarfile):
        self.tarfile = tarfile

    @property
    def crane_metadata_file(self):
        return crane-metadata.json

    @property
    def docker_image_tarfile(self):
        return self.tarfile

class AwsS3(object):
    def __init__(self, bucket, app, image_tar):
        self.bucket = bucket
        self.app = app
        self.image_tar = image_tar

    @property
    def key(self):
        return self.app + "/" + self.image_tar

    def upload_tar(self):
        s3 = boto.connect_s3()
        bucket = s3.create_bucket(self.bucket)
        k = Key(bucket)
        k.key = self.key
        k.set_contents_from_filename(self.image_tar)
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
    s3 = AwsS3(args.bucket_name, args.app_name, pulp.docker_image_tarfile)
    s3.upload_tar()


if __name__ == '__main__':
    main()


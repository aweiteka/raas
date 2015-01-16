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
import urlparse
import base64

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

    def get_tarfile(self):
        """Get a tarfile plus json metadata from url or local file"""
        parts = urlparse.urlsplit(self.tarfile)
        if not parts.scheme or not parts.netloc:
            print "Using local file %s" % self.tarfile
            self.extract_tar(self.tarfile)
        else:
            from urllib2 import Request, urlopen, URLError
            req = Request(self.tarfile)
            try:
                print "Fetching file via URL %s" %  self.tarfile
                response = urlopen(req)
            except URLError as e:
                if hasattr(e, 'reason'):
                    print 'We failed to reach a server.'
                    print 'Reason: ', e.reason
                elif hasattr(e, 'code'):
                    print 'The server couldn\'t fulfill the request.'
                    print 'Error code: ', e.code
            else:
                raw_tarfile = tempfile.NamedTemporaryFile(mode='wb', suffix='.tar')
                raw_tarfile.write(response.read())
                print "Write file %s from URL" % raw_tarfile.name
                self.extract_tar(raw_tarfile.name)

    @property
    def docker_images_dir(self):
        """Temp dir of docker images"""
        return self.tar_tempdir + "/web"

    def extract_tar(self, image_tarfile):
        """Extract tarfile into temp dir"""
        tar = tarfile.open(image_tarfile)
        tar.extractall(path=self.tar_tempdir)
        print "Extracted tarfile to %s" % self.tar_tempdir
        print self.crane_metadata_file
        tar.close()

class AwsS3(object):
    """Interactions with AWS S3"""
    def __init__(self, **kwargs):
        self.bucket = kwargs['bucket_name']
        self.app = kwargs['app_name']
        self.images_dir = kwargs['images_dir']
        self.mask_layers = kwargs['mask_layers']

    def upload_layers(self, files):
        """Upload image layers to S3 bucket"""
        s3 = boto.connect_s3()
        bucket = s3.create_bucket(self.bucket)
        print "Created S3 bucket %s" % self.bucket
        print "Uploading image layers to S3"
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
    """Interact with Openshift REST API"""
    def __init__(self, **kwargs):
        # auth_token supported?
        self.auth_token = kwargs['auth_token']
        self.username = kwargs['username']
        self.password = kwargs['password']
        self.server_url = kwargs['server_url']
        self.app_git_url = kwargs['app_git_url']
        self.domain = kwargs['domain']
        self.cartridge = kwargs['cartridge']
        #FIXME:
        self.app_name = "registry"
        self.app_data = None
        #self.cranefile = cranefile

    @property
    def credentials(self):
        return base64.encodestring('%s:%s' % (self.username, self.password))[:-1]

    @property
    def env_vars(self):
        return [("OPENSHIFT_PYTHON_WSGI_APPLICATION", "crane/wsgi.py"), ("OPENSHIFT_PYTHON_DOCUMENT_ROOT", "crane/")]

    def call_openshift(self, url, req_type="get", payload=None):
        if req_type in "get":
            r = requests.get(url, auth=requests.auth.HTTPBasicAuth(self.username, self.password))
        else:
            r = requests.post(url, auth=requests.auth.HTTPBasicAuth(self.username, self.password), data=payload)
        return r

    def create_app(self):
        """Create an Openshift application"""
        payload = {"name": self.app_name,
                   "cartridge": self.cartridge,
                   #"scale": True,
                   "initial_git_url": self.app_git_url}
        url = self.server_url + "/broker/rest/domains/" + self.domain + "/applications"
        print "Creating OpenShift application"
        r = self.call_openshift(url, "post", payload)
        print "Created app %s" % self.app_name
        #self.app_id = r.text['data']['id']
        text = r.json()
        #print json.dumps(r.json(), indent=4)
        self.app_data = text['data']
        self.set_env_vars(text['data']['links']['ADD_ENVIRONMENT_VARIABLE']['href'])
        self.restart_app()

    def set_env_vars(self, url):
        for var in self.env_vars:
            payload = {"name": var[0],
                       "value": var[1]}
            r = self.call_openshift(url, "post", payload)
            print "Setting environment variable %s" % var[0]

    def restart_app(self):
        payload = {"event": "restart"}
        r = self.call_openshift(self.app_data['links']['RESTART']['href'], "post", payload)
        print "restarting application"

    def update_git_repo(self):
        #git clone self.app_git_url
        #cp self.cranefile
        return

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
                       metavar='MYAPP.TAR or https://pulp-server.example.com/pulp/static/myapp.tar',
                       help='Local file or URL of Pulp export being uploaded')


    args = parser.parse_args()

    config = ConfigParser.ConfigParser()
    config.read('raas.cfg')
    mask_layers = config.get('redhat', 'mask_layers')
    mask_layers = re.split(',| |\n', mask_layers.strip())
    pulp = PulpTar(args.tarfile)
    pulp.get_tarfile()
    kwargs = {"bucket_name": args.bucket_name,
              "app_name": args.app_name,
              "images_dir": pulp.docker_images_dir,
              "mask_layers": mask_layers}
    s3 = AwsS3(**kwargs)
    files = s3.walk_dir(pulp.docker_images_dir)
    s3.upload_layers(files)
    #cranefile = pulp.crane_metadata_file
    os = Openshift(**config._sections['openshift'])
    os.create_app()


if __name__ == '__main__':
    main()


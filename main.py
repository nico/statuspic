#!/usr/bin/env python

import os
import urllib
import urllib2
import urlparse
import webapp2

from google.appengine.ext import blobstore
from google.appengine.ext.webapp import blobstore_handlers

from google.appengine.ext import db

from google.appengine.api import images

from google.appengine.ext.webapp.mail_handlers import InboundMailHandler


class Photo(db.Model):
    created = db.DateTimeProperty(auto_now_add=True)
    blob_key = blobstore.BlobReferenceProperty(required=True)
    # XXX: width / height?


main_html_head = """\
<!doctype html>
<html lang="en"> 
<head>
<meta charset="utf-8">
<title>Album</title>
<style>
body {
  max-width: 800px;
  margin: auto;
}
img {
  vertical-align: text-bottom;
}
</style>
</head>
<body>
"""
content_types = "image/jpeg,image/png"
main_html = """\
<hr>
<form action="%s" method="POST" enctype="multipart/form-data">
Upload File:
<input type="file" name="file" accept="%s" multiple
onchange="if (this.value) this.parentNode.submit();"><br>
<!--<input type="submit">-->
</form>
<form action="%s" method="POST">
Grab File from Web: <input type="text" name="url"><input type="submit">
</form>
<p>Or email images to <a href="mailto:mail@statuspic.appspotmail.com"
>mail@statuspic.appspotmail.com</a>
"""

class MainHandler(webapp2.RequestHandler):
    def get(self):
        self.response.out.write(main_html_head)
        grab_url = "grab"
        pics = db.GqlQuery("select * from Photo order by created desc limit 12")
        for pic in pics:
            #url = 'serve/%s' % pic.blob_key.key()
            url = 'id/%s' % pic.key().id()
            w = 200
            thumb_url = images.get_serving_url(pic.blob_key, size=w, crop=True)
            self.response.out.write(
                '<a href="%s"><img src="%s" width="%d" height="%d"></a\n>' %
                (url, thumb_url, w, w))

        # Needs to be an absolute URL!
        upload_url = blobstore.create_upload_url('/upload')
        self.response.out.write(main_html %
                                (upload_url, content_types, grab_url))


class UploadHandler(blobstore_handlers.BlobstoreUploadHandler):
    def post(self):
        # 'file' is file upload field in the form
        upload_files = self.get_uploads('file')
        for blob_info in upload_files:
            if blob_info.content_type not in content_types.split(','):
                # Ignore non-images.
                blob_info.delete()
                continue
            # XXX: filter out dupes
            Photo(blob_key=blob_info.key()).put()

        # Needs to be an absolute URL!
        self.redirect('/')


class ServeHandler(blobstore_handlers.BlobstoreDownloadHandler):
    def get(self, resource):
        resource = str(urllib.unquote(resource))
        blob_info = blobstore.BlobInfo.get(resource)
        self.send_blob(blob_info)


class ServeIdHandler(blobstore_handlers.BlobstoreDownloadHandler):
    def get(self, resource):
        photo = Photo.get_by_id(int(resource))
        if not photo: return
        self.send_blob(photo.blob_key)


# NOTE: This is an experimental, unsupported api.
from google.appengine.api import files

class ReceiveMailHandler(InboundMailHandler):
    def receive(self, received_mail):
        # XXX: Look at HTML input, grab <img> tags.

        # http://code.google.com/p/googleappengine/issues/detail?id=6342
        if not hasattr(message, 'attachments'):
            return

        for name, contents in received_mail.attachments:
            _, ext = os.path.splitext(name)
            ext = ext.lower()
            if ext not in ['.png', '.jpg', '.jpeg']:
              continue
            # XXX should content-sniff too
            # XXX could probably get mimetype from attachment headers using
            # received_mail.original (a email.message.Message) somehow
            mime_type = {
              '.png': 'image/png',
              '.jpg': 'image/jpeg',
              '.jpeg': 'image/jpeg',
            }[ext]
            # XXX Setting _blobinfo_uploaded_filename is extra-unsupported.
            file_name = files.blobstore.create(mime_type=mime_type,
                                               _blobinfo_uploaded_filename=name)
            with files.open(file_name, 'a') as f:
              # XXX: filter out dupes
              f.write(contents.decode())  # Hope for the best!
            files.finalize(file_name)

            blob_key = files.blobstore.get_blob_key(file_name)
            Photo(blob_key=blob_key).put()

class GrabHandler(webapp2.RequestHandler):
    def post(self):
        url = self.request.get('url', '')
        name = os.path.basename(urlparse.urlparse(url).path)

        _, ext = os.path.splitext(name)
        ext = ext.lower()
        if ext in ['.png', '.jpg', '.jpeg']:
            data = urllib2.urlopen(url).read()

            # XXX should content-sniff too
            # XXX could probably get mimetype from url request
            mime_type = {
              '.png': 'image/png',
              '.jpg': 'image/jpeg',
              '.jpeg': 'image/jpeg',
            }[ext]
            # XXX Setting _blobinfo_uploaded_filename is extra-unsupported.
            file_name = files.blobstore.create(mime_type=mime_type,
                                               _blobinfo_uploaded_filename=name)
            with files.open(file_name, 'a') as f:
              # XXX: filter out dupes
              f.write(data)  # Hope for the best!
            files.finalize(file_name)

            blob_key = files.blobstore.get_blob_key(file_name)
            Photo(blob_key=blob_key).put()
            
        self.redirect('/')


app = webapp2.WSGIApplication([
    ('/', MainHandler),
    ('/upload', UploadHandler),
    ('/serve/([^/]+)?', ServeHandler),
    ('/id/([^/]+)?', ServeIdHandler),
    ('/grab', GrabHandler),
    ReceiveMailHandler.mapping(),
    ], debug=True)

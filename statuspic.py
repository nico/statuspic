#!/usr/bin/env python

import logging
import os
import urllib
import urllib2
import urlparse
import webapp2

from google.appengine.ext import blobstore
from google.appengine.ext.webapp import blobstore_handlers

from google.appengine.ext import db

from google.appengine.api import images
from google.appengine.api import memcache

from google.appengine.ext.webapp.mail_handlers import InboundMailHandler

import peekimagedata


class Photo(db.Model):
    created = db.DateTimeProperty(auto_now_add=True)
    blob_key = blobstore.BlobReferenceProperty(required=True)
    width = db.IntegerProperty(required=True)
    height = db.IntegerProperty(required=True)

    # Computing serving urls takes > 50ms, so precompute them. They don't
    # expire: https://groups.google.com/group/google-appengine/browse_thread/thread/9525f68cbe04d165
    image_serving_url = db.StringProperty(required=True)

    def serving_url(self, size=None, crop=False):
        # get_serving_url() without a size requests a size=512 image, so
        # pass the image size explicitly.
        # https://groups.google.com/group/google-appengine/browse_thread/thread/35de7b4ed8a99d18
        if not size:
            size = max(self.width, self.height)
        suffix = '=s%d' % size
        if crop: suffix += '-c'
        return self.image_serving_url + suffix

    @staticmethod
    def cached_by_id(photo_id):
        key = 'photo_' + str(photo_id)
        photo = memcache.get(key)
        if not photo:
            photo = Photo.get_by_id(photo_id)
            memcache.set(key, photo)
        return photo

def store_blob(blob_key, width, height):
    # XXX: filter out dupes
    serving_url = images.get_serving_url(blob_key)
    photo = Photo(
        blob_key=blob_key, width=width, height=height, serving_url=serving_url)
    photo.put()
    get_main_html(update=True)  # Update cache.


main_html_head = """\
<!doctype html>
<html lang="en"> 
<head>
<meta charset="utf-8">
<title>statuspic</title>
<style>
body {
  max-width: 800px;
  margin: 20px auto;
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

def get_pics(update=False):
    key = 'main_page_pics'
    pics = memcache.get(key)
    if not pics or update:
        pics = db.GqlQuery("select * from Photo order by created desc limit 12")
        pics = list(pics)
        memcache.set(key, pics)
    return pics


def build_main_html(update=False):
    pics = get_pics()
    result = []
    result.append(main_html_head)
    grab_url = "/grab"
    for pic in pics:
        url = '/i/%s' % pic.key().id()
        w = 200
        thumb_url = pic.serving_url(size=w, crop=True)
        result.append(
            '<a href="%s"><img src="%s" width="%d" height="%d"></a\n>' %
            (url, thumb_url, w, w))

    # Needs to be an absolute URL!
    upload_url = blobstore.create_upload_url('/upload')
    result.append(main_html % (upload_url, content_types, grab_url))
    return ''.join(result)


def get_main_html(update=False):
    key = 'main_page_html'
    html = memcache.get(key)
    if not html or update:
        html = build_main_html(update)
        memcache.set(key, html)
    return html
    

class MainHandler(webapp2.RequestHandler):
    def get(self):
        self.response.write(get_main_html())

class UploadHandler(blobstore_handlers.BlobstoreUploadHandler):
    def post(self):
        # 'file' is file upload field in the form
        upload_files = self.get_uploads('file')
        for blob_info in upload_files:
            if blob_info.content_type not in content_types.split(','):
                # Ignore non-images.
                logging.warning("Invalid mimetype %s, skipping"
                                % blob_info.content_type)
                blob_info.delete()
                continue

            # images.Image doesn't have width and height when built from a
            # blob_key.  The documentation doesn't state that it's safe to
            # build an images.Image with partial data, so manually sniff image
            # dimensions.
            # http://thejapanesepage.com/phpbb/download/file.php?id=247 needs
            # at least 150kB of image data.
            data = blobstore.fetch_data(blob_info, 0, 200000) 
            try:
                width, height = peekimagedata.peek_dimensions(data)
                mimetype = peekimagedata.peek_mimetype(data)
            except ValueError:
                logging.warning("Failed to peek, skipping")
                blob_info.delete()
                continue

            if blob_info.content_type != mimetype:
                logging.warning("Sniffed mimetype didn't match, skipping")
                blob_info.delete()
                continue
            
            store_blob(blob_info.key(), width, height)

        self.redirect('/')


class ServeHandler(blobstore_handlers.BlobstoreDownloadHandler):
    def get(self, resource):
        resource = str(urllib.unquote(resource))
        blob_info = blobstore.BlobInfo.get(resource)
        self.send_blob(blob_info)


class ServeIdHandler(blobstore_handlers.BlobstoreDownloadHandler):
    def get(self, resource):
        photo = Photo.cached_by_id(int(resource))
        if not photo: return
        # Serving from photo.serving_url() would be a lot faster,
        # but redirecting to there leaks the serving URL to the user. Since
        # most people hopefully won't click through to the image, take the
        # speed hit.
        self.send_blob(photo.blob_key)

# Note: Instead of centering by setting an explicit width on body, wrapping
# image and g+ button in a div, setting body to text-align:center and the div
# to display:inline-block;text-align:left works too. However, that leads to
# flashes if the image is less wide than the g+ button and the image loads first
# (it usually does).
# http://www.google.com/webmasters/+1/button/
image_html = '''\
<style>
body {
  width: %dpx;
  margin: 20px auto;
}
</style>
<a href="%s"><img src="%s" width="%d" height="%d"></a>
<p><g:plusone size="medium" annotation="inline"></g:plusone></p>

<script type="text/javascript">
  (function() {
    var po = document.createElement('script'); po.type = 'text/javascript'; po.async = true;
    po.src = 'https://apis.google.com/js/plusone.js';
    var s = document.getElementsByTagName('script')[0]; s.parentNode.insertBefore(po, s);
  })();
</script>
'''
class ServeImageHandler(webapp2.RequestHandler):
    def get(self, resource):
        photo = Photo.cached_by_id(int(resource))
        if not photo:
            self.abort(404)

        # Images from get_serving_url() can be served at 0.5MB / 50ms. Serving
        # the same image through a BlobstoreDownloadHandler takes 3s for the
        # same image.
        url = '../id/%s' % photo.key().id()
        img_url = photo.serving_url()
        self.response.out.write(
            image_html % (photo.width, url, img_url, photo.width, photo.height))


# NOTE: This is an experimental, unsupported api.
from google.appengine.api import files

def write_image_blob(data, name):
    _, ext = os.path.splitext(name)
    ext = ext.lower()
    if ext not in ['.png', '.jpg', '.jpeg']:
        logging.warning("invalid extension on '%s', skipping" % name)
        return

    mimetype = {
      '.png': 'image/png',
      '.jpg': 'image/jpeg',
      '.jpeg': 'image/jpeg',
    }[ext]

    try:
      sniffed_mimetype = peekimagedata.peek_mimetype(data)
      width, height = peekimagedata.peek_dimensions(data)
    except ValueError:
      logging.warning("Failed to get dimensions/mimetype, skipping '%s'" % name)
      return

    if sniffed_mimetype != mimetype:
        logging.warning("Invalid mimetype (%s, %s), skipping '%s'" %
                        (sniffed_mimetype, mimetype, name))
        return

    # Note: Setting _blobinfo_uploaded_filename is extra-unsupported.
    file_name = files.blobstore.create(mime_type=mimetype,
                                       _blobinfo_uploaded_filename=name)
    with files.open(file_name, 'a') as f:
      # XXX: filter out dupes
      f.write(data)
    files.finalize(file_name)

    blob_key = files.blobstore.get_blob_key(file_name)
    store_blob(blob_key, width, height)


class ReceiveMailHandler(InboundMailHandler):
    def receive(self, received_mail):

        # XXX: Look at HTML input, grab <img> tags.

        # http://code.google.com/p/googleappengine/issues/detail?id=6342
        if not hasattr(message, 'attachments'):
            logging.warning("No attachment on email")
            return

        for name, contents in received_mail.attachments:
            write_image_blob(contents.decode(), name)


class GrabHandler(webapp2.RequestHandler):
    def post(self):
        url = self.request.get('url', '')
        name = self.request.get('name', '')
        if not name:
            name = os.path.basename(urlparse.urlparse(url).path)

        _, ext = os.path.splitext(name)
        ext = ext.lower()
        if ext in ['.png', '.jpg', '.jpeg']:
            data = urllib2.urlopen(url).read()
            write_image_blob(data, name)
        else:
            logging.warning("invalid extension on '%s', skipping" % name)
            
        self.redirect('/')


class ListAllHandler(webapp2.RequestHandler):
    def get(self):
        self.response.headers['Content-Type'] = 'text/plain'
        pics = db.GqlQuery("select * from Photo order by created desc")
        for pic in pics:
            url = urlparse.urljoin(self.request.uri,'i/%s' % pic.key().id())
            self.response.write(url + '\n')


app = webapp2.WSGIApplication([
    ('/', MainHandler),
    ('/upload', UploadHandler),
    ('/serve/([^/]+)?', ServeHandler),
    ('/id/([^/]+)?', ServeIdHandler),
    ('/grab', GrabHandler),
    ('/i/([^/]+)?', ServeImageHandler),
    ('/listall', ListAllHandler),
    ReceiveMailHandler.mapping(),
    ], debug=True)

import urllib
import os.path
import docutils.transforms
from docutils import nodes
from config import POSTS_LOCATION, IMAGES_LOCATION, TEMP_DIRECTORY, TEMP_FILES
try:
    import Image
except ImportError:
    print("WARNING: PIL is absent. Won't be able to automatically scale/rotate images.")
    Image = None

# FIXME: this is kind of a hack.
# I have two transforms here, one which downloads/uploads the image,
# another which scales it. Data is stored willy-nilly; I'm not even
# sure it works correctly if you have a dotrc data backend.
# Scaling is sophisticated -- stores different forms -- but rotating
# is not. Neither does anything during a preview.
class ImageHandlerTransform(docutils.transforms.Transform):
    '''Base class for transforms to come'''
    @property
    def save_uploads(self, *args, **kwargs):
        if not hasattr(self, '_save_uploads'):
            app = self.document.settings.application
            self._save_uploads = app.config.has_option('config', 'save_uploads') and \
                app.config.getboolean('config', 'save_uploads') == True
        return self._save_uploads

    def uri_filename(self, uri):
        # FIXME: this might not work on non-Unix -- but who cares?
        target_filename = os.path.split(uri)[1]
        if '?' in target_filename:
            target_filename = target_filename[:target_filename.index('?')]
        if '.' not in target_filename:
            target_filename = raw_input("Image specified by %s doesn't have a filename.\nWhat would you like this image to be named?\n> "%(uri,))

        return target_filename

    def uploads_dir(self):
        app = self.document.settings.application
        dir = TEMP_DIRECTORY
        if self.save_uploads:
            dir = os.path.join(os.path.dirname(app.filename), 'uploads')
            try: os.mkdir(dir)
            except OSError, e:
                # Probably "file exists"
                if e.errno != 17: raise

        return dir

    def cleanup_file(self, filename):
        if not self.save_uploads and filename.startswith(TEMP_DIRECTORY):
            TEMP_FILES.append(filename)

    @property
    def uri(self):
        return self.startnode.details['uri']

    def upload_image(self, uri, filename, attribute='saved_as'):
        '''Upload ``filename`` as the replacement for ``uri`` in the document.

        Save the resulting filename in the metainfo for the document, using the attribute given by ``attribute``.'''
        wp = self.document.settings.wordpress_instance
        print 'uploading file', uri, filename
        new_uri = wp.upload_file(filename, overwrite=True)

        app = self.document.settings.application
        app.save_info(self.document, 'image '+ uri, attribute, new_uri, IMAGES_LOCATION, image=uri)

    def replace_with_new_image(self, uri, find_url_with='saved_as', details=None):
        app = self.document.settings.application
        new_uri = app.get_directive_info(self.document, 'image', uri, find_url_with)
        self.document.settings.used_images[uri] = True

        if details == None:
            details = self.startnode.details.copy()
        details['uri'] = new_uri
        image = nodes.image(**details)
        # print("Transform of class {transform} splicing in {image}x{image_id} to replace {node}x{node_id}.".format(
        #         transform=self.__class__, image=image, image_id=id(image), node=self.startnode.parent, node_id=id(self.startnode.parent)))
        # print("Startnode was {id}".format(id=id(self.startnode)))
        if self.startnode.parent:
            # Splice in the new image
            self.startnode.parent.replace_self(image)

            # Get rid of this particular transform
            self.startnode.parent.replace(self.startnode, [])

            # Any other pendings?
            for child in self.startnode.parent:
                image += child

    def filename_insert_before_extension(self, filename, suffix):
        head, ext = os.path.splitext(filename)
        new_filename = "{head}-{suffix}{ext}".format(head=head, suffix=suffix, ext=ext)
        return new_filename

class DownloadImageTransform(ImageHandlerTransform):
    default_priority = 100
    def apply(self, **kwargs):
        if self.document.settings.application.preview:
            print "Not downloading image because we're previewing document"
            if self.startnode:
                self.startnode.replace_self([])

            return

        print "replacing image with a downloaded verison"
        self.build_downloaded_node()

    def build_downloaded_node(self):
        # FIXME: disabling for now
        #return nodes.image(uri=self.startnode.details['uri'])
        uri = self.uri

        app = self.document.settings.application
        filename = self.download_image(uri)

        # This is a quick hack.
        # FIXME: rewrite everything
        if 'rotate' in self.startnode.details:
            filename = self.rotate_image(filename)

        self.upload_image(uri, filename)

        self.replace_with_new_image(uri, find_url_with='saved_as')

    def rotate_image(self, filename):
        degrees = self.startnode.details['rotate']
        new_filename = self.filename_insert_before_extension(filename, 'rot{0}'.format(degrees))
        degrees = float(degrees)

        image = Image.open(filename)
        image = image.rotate(degrees)
        image.save(new_filename)
        return new_filename


    def download_image(self, uri):
        app = self.document.settings.application

        target_filename = self.uri_filename(uri)
        dir = self.uploads_dir()

        filename = os.path.join(dir, target_filename)
        if not os.path.exists(filename):
            print("Downloading {0}".format(uri))
            filename, headers = urllib.urlretrieve(uri, os.path.join(dir, target_filename))

        self.cleanup_file(filename)
        return filename

class ScaleImageTransform(ImageHandlerTransform):
    '''Creates a new image by scaling another image'''
    default_priority = 101

    def apply(self):
        scale = self.startnode.details['scale']

        app = self.document.settings.application
        # The URI of the image itself. We can't use details['uri']
        # because when we got a saved_as, the 'uri' points to that.
        # Instead use original URI, put in place by MyImageDirective.
        uri = self.startnode.details['orig_uri']

        # Either way, though, this is the URI where the image now lives
        full_uri = self.startnode.parent['uri']

        # Details for new image node we'll create.
        details = self.startnode.details.copy()

        # Add a target to point to the full-size image we uploaded.
        if 'orig_target' not in self.startnode.details:
            # FIXME: is this right? Doesn't this change stuff in dotrc backends too?
            app.save_info(self.document, 'image '+ uri, 'target', full_uri, IMAGES_LOCATION, image=uri)
            details['orig_target'] = full_uri
        details['target'] = details['orig_target']

        # Create some string-based representations of the image name
        suffix = '{scale}'.format(scale=scale)
        image_name = 'form-{suffix}'.format(suffix=suffix)

        # If this attribute already exists, we don't need to re-generate or re-upload.
        if not app.has_directive_info(self.document, 'image', uri, image_name):
            # Get the filename we expect to find the source, and create
            # a similar filename for the thumbnail.
            target_filename = self.uri_filename(uri)
            dir = self.uploads_dir()
            orig_filename = os.path.join(dir, target_filename)
            new_filename = self.filename_insert_before_extension(orig_filename, suffix)

            if not os.path.exists(orig_filename):
                print("Scaling image without a local source: redownloading {0} to {1}".format(uri, orig_filename))
                #filename, headers = urllib.urlretrieve(uri, os.path.join(dir, target_filename))

            self.process_image(orig_filename, new_filename, scale=scale)
            self.cleanup_file(new_filename)

            self.upload_image(uri, new_filename, attribute=image_name)

        self.replace_with_new_image(uri, find_url_with=image_name, details=details)

    def process_image(self, orig_filename, new_filename, scale=None):
        print("Scaling image from {0} to {1}".format(orig_filename, new_filename))
        image = Image.open(orig_filename)
        if scale:
            dimensions = factor = None
            try:
                factor = float(scale)
                dimensions = image.size
                dimensions = int(dimensions[0]*factor), int(dimensions[1]*factor)
            except ValueError, e:
                dimensions = scale.split('x')
                dimensions = int(dimensions[0]), int(dimensions[1])

        image.thumbnail(dimensions, Image.ANTIALIAS)
        image.save(new_filename)
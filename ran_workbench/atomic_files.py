"""Publish a fully written sibling file without racing an existence check."""
import os


def publish_file(temporary, destination, *, overwrite=False):
    if overwrite:
        os.replace(temporary, destination)
        return
    try:
        os.link(temporary, destination)
    except FileExistsError:
        raise
    except OSError as error:
        # Do not fall back to exclusive-open plus copy: readers could observe
        # partial content. The caller removes its temporary on every path.
        raise OSError('Atomic exclusive publication requires hard-link support: ' + str(error)) from error

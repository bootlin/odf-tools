#!/usr/bin/env python
#
# url_cop: URLs checking on PDF documents
# Reports broken hyperlinks in PDF documents
#
# About page: TODO
# Downloads page: TODO
#
# Copyright (C) 2012 Free Electrons
# Author: Ezequiel Garcia <ezequiel.garcia at free-electrons com>
#
#   URL checking based on 'coool' script
#   Copyright (C) 2005-2012 Free Electrons
#   Author: Michael Opdenacker <michael at free-electrons com>
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the
# Free Software Foundation; either version 2 of the License, or (at your
# option) any later version.
#
# THIS SOFTWARE IS PROVIDED ``AS IS'' AND ANY EXPRESS OR IMPLIED
# WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF
# MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN
# NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT
# NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF
# USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
# ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF
# THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#
# You should have received a copy of the  GNU General Public License along
# with this program; if not, write  to the Free Software Foundation, Inc.,
# 675 Mass Ave, Cambridge, MA 02139, USA.
#
#########################################################3
#
# TODO
# ----
#   * Apply a PEP8 checker
#

import os
import re
import sys
import time
import threading
import logging as log
import httplib
import urllib2
import urllib
import urlparse
from HTMLParser import HTMLParser
from pdfminer.pdfparser import PDFDocument, PDFParser
from pdfminer.pdftypes import PDFStream
from optparse import OptionParser


##########################################################
# Common routines
##########################################################

def touch(fname):
    # Implementation of the touch command
    if os.path.exists(fname):
        os.utime(fname, None)
    else:
        open(fname, 'w').close()


def url_fix(s, charset='utf-8'):
    # This snippet idea has been taken from 'Werkzeug' project.
    # See: http://werkzeug.pocoo.org/
    #
    # Sometimes you get an URL by a user that just isn't a real
    # URL because it contains unsafe characters like ' ' and so on.
    # This function can fix some of the problems in a similar way browsers
    # handle data entered by the user.

    if isinstance(s, unicode):
        s = s.encode(charset, 'ignore')
    scheme, netloc, path, qs, anchor = urlparse.urlsplit(s)
    path = urllib.quote(path, '/%')
    qs = urllib.quote_plus(qs, ':&=')
    return urlparse.urlunsplit((scheme, netloc, path, qs, anchor))


##########################################################
# URL checking
##########################################################

def check_http_url(url):
    do_url = url_fix(url)
    request = urllib2.Request(do_url)
    opener = urllib2.build_opener()
    # Add a browser-like User-Agent.
    # Some sites (like wikipedia) don't seem to accept requests
    # with the default urllib2 User-Agent
    request.add_header('User-Agent',
                       'Mozilla/5.0 (X11; U; Linux i686; en-US;'
                       'rv \u2014 1.7.8) Gecko/20050511')

    try:
        opener.open(request).read()
    except urllib2.HTTPError, why:
        return (False, why)
    except urllib2.URLError, why:
        return (False, why)
    except httplib.BadStatusLine, why:
        return (False, why)
    return (True, None)


def check_ftp_url(url):
    # urllib2 doesn't raise any exception when a
    # ftp url for an invalid file is given
    # Only invalid domains are reported
    # That's why we are using urllib.urlretrieve

    do_url = url_fix(url)
    try:
        tmpfile = urllib.urlretrieve(do_url)[0]
    except IOError, why:
        return (False, why)
    else:
        # With a non-existing file on a ftp URL,
        # we get a zero size output file
        # Even if a real zero size file exists,
        # it's worth highlighting anyway
        # (no point in making an hyperlink to it)
        if os.path.getsize(tmpfile) == 0:
            os.remove(tmpfile)
            return (False, 'Non existing or empty file')
    return (True, None)


def check_url(url):

    log.debug('Checking link: {}'.format(url))

    protocol = urlparse.urlparse(url)[0]

    # TODO:
    # I **think** PDF also has 'internal links',
    # should we check for those?
    if protocol == 'http' or protocol == 'https':
        res = check_http_url(url)
    elif protocol == 'ftp':
        res = check_ftp_url(url)
    else:
        # Still try to handle other protocols
        try:
            urllib2.urlopen(url)
            res = (True, None)
        except:
            res = (False, 'Unknown - Please report this bug!')

    log.debug('Done checking link {}'.format(url))
    return res


def get_hostname(url):

    return urlparse.urlparse(url)[1]


def url_is_ignored(url, exclude_hosts):

    hostname = get_hostname(url)
    return exclude_hosts.count(hostname)


def check_url_threaded(url, errors, lock, tokens_per_host, options):

    # Check that we don't run too many parallel checks per host
    # That could bring the host down or at least be considered
    # as the Denial of Service attack.

    hostname = get_hostname(url)

    log.info('Checking hyperlink: {}'.format(url))

    # Counting parallel requests to the same host.
    # Of course, ignore local links

    if hostname != '':

        # dictionaries are thread-safe,
        # but it's best to put a lock here anyway
        with lock:
            if hostname in tokens_per_host:
                tokens_per_host[hostname] = options.max_requests_per_host

        while True:
            with lock:
                if tokens_per_host[hostname] > 0:
                    tokens_per_host[hostname] -= 1
                    break
            time.sleep(1)

    # Do the URL check!
    res, reason = check_url(url)
    if res is False:
        with lock:
            errors.append((url, reason))

    if hostname != '':
        with lock:
            tokens_per_host[hostname] += 1


##########################################################
# URL extraction
##########################################################

ESC_PAT = re.compile(r'[\000-\037&<>()"\042\047\134\177-\377]')


def e(s):
    return ESC_PAT.sub(lambda m: '&#%d;' % ord(m.group(0)), s)


def search_url_string(obj):
    if isinstance(obj, str):
        return e(obj)


def search_url(obj, urls):
    if obj is None:
        return

    if isinstance(obj, dict):
        for (k, v) in obj.iteritems():

            # A dictionary with a "URI" key
            # may contain an URL string
            if k == 'URI':
                url = search_url_string(v)
                # Need to unescape html special characters
                parser = HTMLParser()
                url = parser.unescape(url)
                if url is not None:
                    log.debug('URL found: {}'.format(url))
                    urls.add(url)

            search_url(v, urls)

    elif isinstance(obj, list):
        for v in obj:
            search_url(v, urls)

    elif isinstance(obj, PDFStream):
        search_url(obj.attrs, urls)


def extract_urls(filename, urls):

    log.info('Checking links in file {} ...'.format(filename))

    # PDFMiner setup shamelessly taken from dumppdf.py
    doc = PDFDocument()
    fp = file(filename, 'rb')
    parser = PDFParser(fp)
    parser.set_document(doc)
    doc.set_parser(parser)
    doc.initialize()

    # Iterate through each object and search for URLs there
    for xref in doc.xrefs:
        for objid in xref.get_objids():
            try:
                obj = doc.getobj(objid)
                search_url(obj, urls)
            except:
                raise

    fp.close()
    return


##########################################################
# Main program
##########################################################

def main():

    global found_errors, options

    # Command parameters
    # Either default values, found in a configuration file
    # or on the command line
    #

    usage = 'usage: %prog [options] [PDF document files]'
    description = 'Reports broken hyperlinks in PDF documents'

    optparser = OptionParser(usage=usage, version='0.1',
                             description=description)

    optparser.add_option('-v', '--verbose',
                         action='store_true', dest='verbose', default=False,
                         help='display progress information')

    optparser.add_option('-s', '--status',
                         action='store_true', dest='status', default=False,
                         help='store check status information'
                         'in a .checked file')

    optparser.add_option('-d', '--debug',
                         action='store_true', dest='debug', default=False,
                         help='display debug information')

    optparser.add_option('-t', '--max-threads',
                         action='store', type='int',
                         dest='max_threads', default=100,
                         help='set the maximum number'
                         'of parallel threads to create')

    optparser.add_option('-r', '--max-requests-per-host',
                         action='store', type='int',
                         dest='max_requests_per_host', default=5,
                         help='set the maximum number'
                         'of parallel requests per host')

    optparser.add_option('-x', '--exclude-hosts',
                         action='store', type='string',
                         dest='exclude_hosts', default='',
                         help='ignore urls which host name'
                         'belongs to the given list')

    (options, args) = optparser.parse_args()

    if options.debug:
        level = log.DEBUG
    elif options.verbose:
        level = log.INFO
    else:
        level = log.WARNING

    log.basicConfig(stream=sys.stderr,
                    level=level,
                    format='%(levelname)s: %(message)s')

    if len(args) == 0:
        log.critical('No files to check. Exiting.')
        sys.exit()

    # Turn options.exclude_hosts into a list, for exact matching
    options.exclude_hosts = options.exclude_hosts.split()

    # Iterate on all given documents, filling urls set
    urls = set()
    for input_file in args:
        extract_urls(input_file, urls)

    if len(urls) == 0:
        log.critical('No URLs found! Exiting.')
        sys.exit()

    # Run URL checks
    found_errors = False
    tokens_per_host = {}
    errors = []
    lock = threading.Lock()
    for url in urls:

        if url_is_ignored(url, options.exclude_hosts):
            continue

        # Do not exceed the max number of threads
        while threading.active_count() - 1 > options.max_threads:
            time.sleep(1)

        t = threading.Thread(target=check_url_threaded,
                             args=(url, errors, lock,
                             tokens_per_host, options))
        t.start()

    # Wait for all threads to complete
    for thread in threading.enumerate():
        log.info('Waiting for URL checks '
                 'to complete: {} threads left'.
                 format(threading.active_count()-1))
        if thread is not threading.current_thread():
                thread.join()

    if len(errors) > 0:
        for url, reason in errors:
            log.error('URL {} failed. Reason: {}'.format(url, reason))
        sys.exit(1)

    if options.status:
        touch('.' + os.path.basename(input_file) + '.checked')

    log.info('Hyperlink checking successful')

    sys.exit(0)


if __name__ == "__main__":
    main()

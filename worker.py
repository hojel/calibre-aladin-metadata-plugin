#!/usr/bin/env python
# vim:fileencoding=UTF-8:ts=4:sw=4:sta:et:sts=4:ai
from __future__ import (unicode_literals, division, absolute_import, print_function)

__license__   = 'GPL v3'
__copyright__ = '2013, hojel'
__docformat__ = 'restructuredtext ko'

import socket, re, datetime
from collections import OrderedDict
from threading import Thread

from lxml.html import fromstring, tostring

from calibre.ebooks.metadata.book.base import Metadata
from calibre.library.comments import sanitize_comments_html
from calibre.utils.cleantext import clean_ascii_chars

class Worker(Thread): # Get details

    '''
    Get book details from Aladin book page in a separate thread
    '''

    def __init__(self, url, result_queue, browser, log, relevance, plugin, timeout=20):
        Thread.__init__(self)
        self.daemon = True
        self.url, self.result_queue = url, result_queue
        self.log, self.timeout = log, timeout
        self.relevance, self.plugin = relevance, plugin
        self.browser = browser.clone_browser()
        self.cover_url = self.isbn = None

    def run(self):
        try:
            self.get_details()
        except:
            self.log.exception('get_details failed for url: %r'%self.url)

    def get_details(self):
        try:
            self.log.info('Aladin url: %r'%self.url)
            raw = self.browser.open_novisit(self.url, timeout=self.timeout).read().strip()
        except Exception as e:
            if callable(getattr(e, 'getcode', None)) and \
                    e.getcode() == 404:
                self.log.error('URL malformed: %r'%self.url)
                return
            attr = getattr(e, 'args', [None])
            attr = attr if attr else [None]
            if isinstance(attr[0], socket.timeout):
                msg = 'Aladin timed out. Try again later.'
                self.log.error(msg)
            else:
                msg = 'Failed to make details query: %r'%self.url
                self.log.exception(msg)
            return

        raw = raw.decode('euc-kr', errors='replace')
        #open('P:\\aladin.html', 'wb').write(raw)

        if 'HTTP 404.' in raw:
            self.log.error('URL malformed: %r'%self.url)
            return

        try:
            root = fromstring(clean_ascii_chars(raw))
        except:
            msg = 'Failed to parse Aladin details page: %r'%self.url
            self.log.exception(msg)
            return

        self.parse_details(root)

    def parse_details(self, root):
        try:
            isbn = self.extract_isbn(self.url)
        except:
            self.log.exception('No ISBN in URL: %r'%self.url)
            isbn = None

        try:
            (title, series, series_index) = self.parse_title_series(root)
        except:
            self.log.exception('Error parsing title and series for url: %r'%self.url)
            title = series = series_index = None

        try:
            authors = self.parse_authors(root)
        except:
            self.log.exception('Error parsing authors for url: %r'%self.url)
            authors = []

        if not title or not authors or not isbn:
            self.log.error('Could not find title/authors/Aladin id for %r'%self.url)
            self.log.error('Aladin: %r Title: %r Authors: %r'%(isbn, title, authors))
            return

        mi = Metadata(title, authors)
        if series:
            mi.series = series
            mi.series_index = series_index
        #mi.set_identifier('isbn', isbn)
        mi.isbn = isbn
        self.isbn = isbn

        # ISBN-13
        try:
            isbn = self.parse_isbn(root)
            if isbn:
                self.isbn = mi.isbn = isbn
        except:
            self.log.exception('Error parsing ISBN for url: %r'%self.url)

        try:
            mi.comments = self.parse_comments(root)
        except:
            self.log.exception('Error parsing comments for url: %r'%self.url)

        try:
            self.cover_url = self.parse_cover(root)
        except:
            self.log.exception('Error parsing cover for url: %r'%self.url)
        mi.has_cover = bool(self.cover_url)
        mi.cover_url = self.cover_url # This is purely so we can run a test for it!!!
        if mi.has_cover:
            self.log.info('Cover URL: '+mi.cover_url)

        try:
            mi.publisher = self.parse_publisher(root)
        except:
            self.log.exception('Error parsing publisher for url: %r'%self.url)

        try:
            mi.pubdate = self.parse_published_date(root)
        except:
            self.log.exception('Error parsing published date for url: %r'%self.url)

        mi.language = 'ko'

        mi.source_relevance = self.relevance

        self.plugin.clean_downloaded_metadata(mi)
        self.result_queue.put(mi)

    def extract_isbn(self, url):
        return re.search('ISBN=(\d+)', url).groups(1)[0]

    def parse_title_series(self, root):
        bgtit_node = root.xpath('//td[@class="pwrap_bgtit"][1]')[0]

        title_node = bgtit_node.xpath('.//a[@class="p_topt01"]')
        if not title_node:
            return (None, None, None)
        title_text = title_node[0].text.strip()

        # 시리즈
        series_node = bgtit_node.xpath('.//a[contains(@href,"SRID=")]')
        if series_node:
            series_grp = [text.strip() for text in series_node[0].text.rsplit(u'시리즈',1)]
            series_name = series_grp[0]
            series_index = float(series_grp[1]) if len(series_grp)==2 and series_grp[1] else None
            return (title_text, series_name, series_index)
        else:
            return (title_text, None, None)

    def parse_authors(self, root):
        bgtit_node = root.xpath('//td[@class="pwrap_bgtit"][1]')[0]

        nodes = bgtit_node.xpath('.//a[@class="np_af" and contains(@href,"AuthorSearch")]')
        return [ node.text.strip() for node in nodes ]

    def parse_isbn(self, root):
        url = root.xpath('//meta[@property="og:url"]/@content')[0]
        if 'ISBN=' in url:
            return url.rsplit('=',1)[1]
        return None

    def parse_publisher(self, root):
        bgtit_node = root.xpath('//td[@class="pwrap_bgtit"][1]')[0]

        nodes = bgtit_node.xpath('.//a[@class="np_af" and contains(@href,"PublisherSearch")]')
        if nodes:
        	return nodes[0].text.strip()

    def parse_published_date(self, root):
        bgtit_node = root.xpath('//td[@class="pwrap_bgtit"][1]')[0]

        date_text = bgtit_node.xpath('.//a[@class="np_af" and contains(@href,"PublisherSearch")]/following-sibling::text()')
        if date_text:
            return self._convert_date_text(date_text[0])

    def _convert_date_text(self, date_text):
        # 2010-01-01
        year_s, month_s, day_s = re.search('(\d{4})-(\d{2})-(\d{2})', date_text).group(1,2,3)
        year = int(year_s)
        month = int(month_s)
        day = int(day_s)
        return datetime.datetime(year, month, day)

    def parse_comments(self, root):
        return root.xpath('//meta[@name="Description"]/@content')[0]

    def parse_cover(self, root):
        page_url = root.xpath('//meta[@property="og:image"]/@content')[0]
        urls = root.xpath('//div[@class="p_previewbox"]/a/img/@src')
        if urls:
        	page_url = urls[0].replace('_fs.','_f.')

        if not self._is_valid_image(page_url):
            self.log.info('Aborting parse_cover')
            return

        self.plugin.cache_identifier_to_cover_url(self.isbn, page_url)
        self.relevance += 5
        return page_url

    def _is_valid_image(self, img_url):
        if img_url.endswith("noimg_b.gif"):
            return False
        return True

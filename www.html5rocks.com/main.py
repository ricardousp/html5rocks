# -*- coding: utf-8 -*-
# Copyright 2010 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License")
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

__author__ = ('kurrik@html5rocks.com (Arne Kurrik) ',
              'ericbidelman@html5rocks.com (Eric Bidelman)')

import common # Need this first to force Django 1.2.

# Standard Imports
import datetime
import logging
import os
import re
import urllib2
import yaml

# Libraries
import html5lib
from html5lib import treebuilders, treewalkers, serializer
from html5lib.filters import sanitizer

from django import http
from django.conf import settings
from django.utils import feedgenerator
from django.utils import simplejson
from django.utils import translation
from django.utils.translation import ugettext as _

# Google App Engine Imports
from google.appengine.api import memcache
from google.appengine.ext import db
from google.appengine.ext import webapp
from google.appengine.ext.webapp import template
from google.appengine.ext.webapp.util import run_wsgi_app


template.register_template_library('templatetags.templatefilters')


class ContentHandler(webapp.RequestHandler):

  def get_language(self):
    lang_match = re.match("^/(\w{2,3})(?:/|$)", self.request.path)
    self.locale = lang_match.group(1) if lang_match else settings.LANGUAGE_CODE
    logging.info("Set Language as %s" % self.locale)
    translation.activate( self.locale )
    return self.locale if lang_match else None

  def browser(self):
    return str(self.request.headers['User-Agent'])

  def is_awesome_mobile_device(self):
    browser = self.browser()
    return browser.find('Android') != -1 or browser.find('iPhone') != -1

  def get_toc(self, path):
    if not (re.search('', path) or re.search('/mobile/', path)):
      return ''

    toc = memcache.get('toc|%s' % path)
    if toc is None or not self.request.cache:
      template_text = template.render(path, {})
      parser = html5lib.HTMLParser(tree=treebuilders.getTreeBuilder("dom"))
      dom_tree = parser.parse(template_text)
      walker = treewalkers.getTreeWalker("dom")
      stream = walker(dom_tree)
      toc = []
      current = None
      for element in stream:
        if element['type'] == 'StartTag':
          if element['name'] in ['h2', 'h3', 'h4']:
            for attr in element['data']:
              if attr[0] == 'id':
                current = {
                  'level' : int(element['name'][-1:]) - 1,
                  'id' : attr[1]
                }
        elif element['type'] == 'Characters' and current is not None:
          current['text'] = element['data']
        elif element['type'] == 'EndTag' and current is not None:
          toc.append(current)
          current = None
      memcache.set('toc|%s' % path, toc, 3600)

    return toc

  def get_feed(self, path):
    articles = memcache.get('feed|%s' % path)
    if articles is None or not self.request.cache:
      # DB query is memcached in get_all().
      tutorials = common.Resource.get_all().order('-publication_date')

      articles = []
      for tut in tutorials:
        article = {}
        article['title'] = tut.title
        article['id'] = '-'.join(tut.title.lower().split())
        article['href'] = tut.url
        article['description'] = tut.description
        article['author_id'] = tut.author.key().name()
        if tut.second_author:
          article['second_author'] = tut.second_author.key().name()
        article['pubdate'] = datetime.datetime.strptime(
                                 str(tut.publication_date), '%Y-%m-%d')
        article['categories'] = []
        for tag in tut.tags:
          article['categories'].append(tag)
        
        articles.append(article)

      memcache.set('feed|%s' % path, articles, 86400) # Cache feed for 24hrs.

    return articles

  def render(self, data={}, template_path=None, status=None,
             message=None, relpath=None):
    if status is not None and status != 200:
      self.response.set_status(status, message)

      # Check if we have a customize error page (template) to display.
      if template_path is None:
        logging.error(message)
        self.response.set_status(status, message)
        self.response.out.write(message)
        return

    current = ''
    if relpath is not None:
      current = relpath.split('/')[0].split('.')[0]

    # Strip out language code from path. Urls changed for i18n work and correct
    # disqus comment thread won't load with the changed urls.
    path_no_lang = re.sub('^\/\w{2,3}\/', '', self.request.path, 1)

    pagename = ''
    if (path_no_lang == ''):
      pagename = 'home'
    else:
      pagename = re.sub('\/', '-', path_no_lang)
      pagename = re.sub('/$|-$', '', pagename)
      pagename = re.sub('^-', '', pagename)

    # Add template data to every request.
    template_data = {
      'toc' : self.get_toc(template_path),
      'self_url': self.request.url,
      'self_pagename': pagename,
      'host': '%s://%s' % (self.request.scheme, self.request.host),
      'is_mobile': self.is_awesome_mobile_device(),
      'current': current,
      'prod': common.PROD,
      'sorted_profiles': common.get_sorted_profiles() # TODO: Don't add profile data on every request.
    }

    template_data['disqus_url'] = template_data['host'] + '/' + path_no_lang

    # Request was for an Atom feed. Render one!
    if self.request.path.endswith('.xml'):
      self.render_atom_feed(template_path, self.get_feed(template_path))
      return

    template_data.update(data)
    if not 'category' in template_data:
      template_data['category'] = _('this feature')

    # Add CORS support entire site.
    self.response.headers.add_header('Access-Control-Allow-Origin', '*')
    self.response.headers.add_header('X-UA-Compatible', 'IE=Edge,chrome=1')
    self.response.out.write(template.render(template_path, template_data))

  def render_atom_feed(self, template_path, data):
    prefix = '%s://%s' % (self.request.scheme, self.request.host)

    feed = feedgenerator.Atom1Feed(
        title=_(u'HTML5Rocks - Tutorials & Updates'),
        link=prefix,
        description=_(u'Take a guided tour through code that uses HTML5.'),
        language=u'en'
        )
    for tut in data:
      author_name = unicode(tut['author_id'])
      if 'second_author' in tut:
        author_name += ',' + tut['second_author']
      feed.add_item(
          title=unicode(tut['title']),
          link=prefix + tut['href'],
          description=unicode(tut['description']),
          pubdate=tut['pubdate'],
          author_name=author_name,
          categories=tut['categories']
          )
    self.response.headers.add_header('Content-Type', 'application/atom+xml')
    self.response.out.write(feed.writeString('utf-8'))

  def post(self, relpath):

    if relpath == 'database/author':
      try:
        given_name = self.request.get('given_name')
        family_name = self.request.get('family_name')
        author = common.Author(
            key_name=''.join([given_name, family_name]).lower(),
            given_name=given_name,
            family_name=family_name,
            org=self.request.get('org'),
            unit=self.request.get('unit'),
            city=self.request.get('city'),
            state=self.request.get('state'),
            country=self.request.get('country'),
            homepage=self.request.get('homepage') or None,
            google_account=self.request.get('google_account') or None,
            twitter_account=self.request.get('twitter_account') or None,
            email=self.request.get('email') or None,
            lanyrd=self.request.get('lanyrd') == 'on')
        lat = self.request.get('lat')
        lon = self.request.get('lon')
        if lat and lon:
          author.geo_location = db.GeoPt(float(lat), float(lon))
        author.put()
      except db.Error:
        pass
      else:
        self.redirect('/database/author')

    elif relpath == 'database/resource':
      author_key = common.Author.get_by_key_name(self.request.get('author'))
      author_key2 = common.Author.get_by_key_name(
          self.request.get('second_author'))

      if author_key.key() == author_key2.key():
        author_key2 = None

      tags = (self.request.get('tags') or '').split(',')
      tags = [x.strip() for x in tags if x.strip()]

      browser_support = [x.lower() for x in
                         (self.request.get_all('browser_support') or [])]

      pub = datetime.datetime.strptime(
          self.request.get('publication_date'), '%Y-%m-%d')
      
      tutorial = common.Resource.get_all().filter('title =', self.request.get('title'))
      if tutorial.count() == 1:
        try:
          tutorial[0].title=self.request.get('title'),
          tutorial[0].description=self.request.get('description'),
          tutorial[0].author=author_key,
          tutorial[0].second_author=author_key2,
          tutorial[0].url=self.request.get('url') or None,
          tutorial[0].browser_support=browser_support,
          tutorial[0].update_date=datetime.date.today(),
          tutorial[0].publication_date=datetime.date(pub.year, pub.month, pub.day),
          tutorial[0].tags=tags
        except TypeError:
          pass
      else:
        try:
          tutorial = common.Resource(
              title=self.request.get('title'),
              description=self.request.get('description'),
              author=author_key,
              second_author=author_key2,
              url=self.request.get('url') or None,
              browser_support=browser_support,
              update_date=datetime.date.today(),
              publication_date=datetime.date(pub.year, pub.month, pub.day),
              tags=tags)
        except TypeError:
          pass        
      tutorial.put()       

      return self.redirect('/database/resource')

    return '/'

  def get(self, relpath):

    # Render uncached verion of page with ?cache=1
    if self.request.get('cache', default_value='1') == '1':
      self.request.cache = True
    else:
      self.request.cache = False

    # Handle humans before locale, to prevent redirect to /en/
    # (but still ensure it's dynamic, ie we can't just redirect to a static
    # url)
    if (relpath == 'humans.txt'):
      self.response.headers['Content-Type'] = 'text/plain'
      sorted_profiles = common.get_sorted_profiles()
      return self.render(data={'sorted_profiles': sorted_profiles,
                               'profile_amount': len(sorted_profiles)},
                         template_path='content/humans.txt',
                         relpath=relpath)

    elif (relpath == 'database/load_resources'):
      self.addTestResources()
      return self.redirect('/database/resource')

    elif (relpath == 'database/load_author_information'):
      self.addTestAuthors()
      return self.redirect('/database/author')
      
    elif 'database/resource' in relpath:
      regex = re.compile("/[^/]+/(\d+)")
      postid = regex.findall(relpath)
      if len(postid) > 0: # /database/resource/1234
        post = common.Resource.get_by_id(int(postid[0]))
        tutorial_form = common.TutorialForm(
            instance=post,
            initial={
              'author': post and post.author.key().name(),
              'second_author': post and post.second_author and post.second_author.key().name() ,
              'browser_support': [x.lower() for x in post.browser_support]
            })
      else: # /database/resource
        tutorial_form = common.TutorialForm()
        
      template_data = {
        'tutorial_form': tutorial_form,
        'resources': common.Resource.get_all().order('-publication_date')
      }
      return self.render(data=template_data,
                         template_path='database/resource_new.html',
                         relpath=relpath)

    elif (relpath == 'database/author'):
      # adds a new author information into DataStore.
      sorted_profiles = common.get_sorted_profiles(update_cache=True)
      template_data = {
        'sorted_profiles': sorted_profiles,
        'profile_amount': len(sorted_profiles),
        'author_form': common.AuthorForm()
      }
      return self.render(data=template_data,
                         template_path='database/author_new.html',
                         relpath=relpath)

    # Get the locale: if it's "None", redirect to English
    locale = self.get_language()
    if not locale:
      return self.redirect("/en/%s" % relpath, permanent=True)

    basedir = os.path.dirname(__file__)

    # Strip off leading `/[en|de|fr|...]/`
    relpath = re.sub('^/?\w{2,3}/', '', relpath)

    # Are we looking for a feed?
    is_feed = self.request.path.endswith('.xml')

    logging.info('relpath: ' + relpath)

    # Setup handling of redirected article URLs: If a user tries to access an
    # article from a non-supported language, we'll redirect them to the
    # English version (assuming it exists), with a `redirect_from_locale` GET
    # param.
    redirect_from_locale = self.request.get('redirect_from_locale', '')
    if not re.match('[a-zA-Z]{2,3}$', redirect_from_locale):
      redirect_from_locale = False
    else:
      translation.activate(redirect_from_locale)
      redirect_from_locale = {
        'lang': redirect_from_locale,
        'msg': _('Sorry, this article isn\'t available in your native '
                 'language; we\'ve redirected you to the English version.')
      }
      translation.activate(locale)

    # Landing page or /tutorials|features|mobile|gaming|business\/?
    if ((relpath == '' or relpath[-1] == '/') or  # Landing page.
        (relpath in ['mobile', 'tutorials', 'features', 'gaming', 'business'] and
        relpath[-1] != '/')):
      path = os.path.join(basedir, 'content', relpath, 'index.html')
    else:
      path = os.path.join(basedir, 'content', relpath)

    # Render the .html page if it exists. Otherwise, check that the Atom feed
    # the user is requesting has a corresponding .html page that exists.

    if (relpath == 'profiles' or relpath == 'profiles/'):
      profiles = common.get_sorted_profiles()
      for p in profiles:
        p['tuts_by_author'] = common.Resource.get_tutorials_by_author(p['id'])
      self.render(data={'sorted_profiles': profiles},
                  template_path='content/profiles.html', relpath=relpath)

    elif re.search('tutorials/casestudies', relpath) and not is_feed:
      # Case Studies look like this on the filesystem:
      #
      #   .../tutorials +
      #                 |
      #                 +-- casestudies   +
      #                 |                 |
      #                 |                 +-- en  +
      #                 |                 |       |
      #                 |                 |       +-- case_study_name.html
      #                 ...
      #
      # So, to determine if an HTML page exists for the requested language
      # `split` the file's path, add in the locale, and check existance:
      logging.info('Building request for casestudy `%s` in locale `%s`',
                   path, locale)
      potentialfile = re.sub('tutorials/casestudies',
                             'tutorials/casestudies/%s' % locale,
                             path)
      englishfile = re.sub('tutorials/casestudies',
                           'tutorials/casestudies/%s' % 'en',
                           path)
      logging.info(englishfile)
      if os.path.isfile(potentialfile):
        logging.info('Rendering in native: %s' % potentialfile)

        self.render(template_path=potentialfile,
                    data={'redirect_from_locale': redirect_from_locale},
                    relpath=relpath)

      # If the localized file doesn't exist, and the locale isn't English, look
      # for an english version of the file, and redirect the user there if
      # it's found:
      elif os.path.isfile(englishfile):
        return self.redirect("/en/%s?redirect_from_locale=%s" % (relpath,
                                                                 locale))

    elif ((re.search('tutorials/.+', relpath) or
           re.search('mobile/.+', relpath) or
           re.search('gaming/.+', relpath) or
           re.search('business/.+', relpath))
          and not is_feed):
      # If no trailing / (e.g. /tutorials/blah/blah), append index.html file.
      if (relpath[-1] != '/' and not relpath.endswith('.html')):
        path += '/index.html'

      # Tutorials look like this on the filesystem:
      #
      #   .../tutorials +
      #                 |
      #                 +-- article-slug  +
      #                 |                 |
      #                 |                 +-- en  +
      #                 |                 |       |
      #                 |                 |       +-- index.html
      #                 ...
      #
      # So, to determine if an HTML page exists for the requested language
      # `split` the file's path, add in the locale, and check existance:
      logging.info('Building request for `%s` in locale `%s`', path, locale)
      (dir, filename) = os.path.split(path)
      if os.path.isfile(os.path.join(dir, locale, filename)):
        self.render(template_path=os.path.join(dir, locale, filename),
                    data={'redirect_from_locale': redirect_from_locale},
                    relpath=relpath)

      # If the localized file doesn't exist, and the locale isn't English, look
      # for an english version of the file, and redirect the user there if
      # it's found:
      elif os.path.isfile( os.path.join(dir, 'en', filename)):
        return self.redirect("/en/%s?redirect_from_locale=%s" % (relpath,
                                                                 locale))
    elif os.path.isfile(path):
      #TODO(ericbidleman): Don't need these tutorial/update results for query.
      if relpath in ['mobile', 'gaming', 'business']:
        result = TagsHandler().get_as_db(relpath)
      else:
        result = common.Resource.get_all().order('-publication_date')

      tutorials = []
      for r in result:
        tutorials.append(r)
        tutorials[-1].classes = [x.replace('class:', '') for x in r.tags
                                 if x.startswith('class:')]
        tutorials[-1].tags = [x for x in r.tags if not x.startswith('class:')]
        tutorials[-1].type = [x for x in r.tags if x.startswith('type:')][0]

      self.render(
          data={'tutorials': tutorials}, template_path=path, relpath=relpath)

    elif os.path.isfile(path[:path.rfind('.')] + '.html'):
      self.render(data={}, template_path=path[:path.rfind('.')] + '.html',
                  relpath=relpath)

    elif os.path.isfile(path + '.html'):
      category = relpath.replace('features/', '')
      data = {
        'category': category,
        'updates': TagsHandler().get_as_db('class:' + category)
      }
      self.render(data=data, template_path=path + '.html', relpath=relpath)

    else:
      self.render(status=404, message='Page Not Found',
                  template_path=os.path.join(basedir, 'templates/404.html'))

  def addTestResources(self):
    memcache.delete('tutorials')

    f = file(os.path.dirname(__file__) + '/tutorials.yaml', 'r')
    for tut in yaml.load_all(f):
      try:
        author_key = common.Author.get_by_key_name(tut['author_id'])
        author_key2 = None
        if 'author_id2' in tut:
          author_key2 = common.Author.get_by_key_name(tut['author_id2'])

        sample = common.Resource(
          title=tut['title'],
          description=tut['description'],
          author=author_key,
          second_author=author_key2,
          url=unicode(tut['url']),
          browser_support=tut['browser_support'],
          update_date=datetime.date.today(),
          publication_date=tut['publication_date'],
          tags=tut['tags'])
        sample.put()
      except TypeError:
        pass # TODO(ericbidelman): Not sure why this is throwing an error, but ignore it.
    f.close()

  def addTestAuthors(self):
    f = file(os.path.dirname(__file__) + '/profiles.yaml', 'r')
    for profile in yaml.load_all(f):
      author = common.Author(
          key_name=unicode(profile['id']),
          given_name=unicode(profile['name']['given']),
          family_name=unicode(profile['name']['family']),
          org=unicode(profile['org']['name']),
          unit=unicode(profile['org']['unit']),
          city=profile['address']['locality'],
          state=profile['address']['region'],
          country=profile['address']['country'],
          google_account=str(profile.get('google')),
          twitter_account=profile.get('twitter'),
          email=profile['email'],
          lanyrd=profile.get('lanyrd', False),
          homepage=profile['homepage'],
          geo_location=db.GeoPt(profile['address']['lat'],
                                profile['address']['lon'])
          )
      author.put()
    f.close()


class APIHandler(webapp.RequestHandler):

  def get(self, relpath):
    if (relpath == 'authors'):
      profiles = {}
      for p in common.get_sorted_profiles(): # This query is memcached.
        profile_id = p['id']
        profiles[profile_id] = p
        geo_location = profiles[profile_id]['geo_location']
        profiles[profile_id]['geo_location'] = str(geo_location)

      self.response.headers['Content-Type'] = 'application/json'
      self.response.out.write(simplejson.dumps(profiles))
      return
    else:
      self.redirect('/')


class TagsHandler(webapp.RequestHandler):

  def _get(self, tag):
    tag = urllib2.unquote(tag)

    # DB query is memcached in get_all().
    return (common.Resource.get_all().filter('tags =', tag)
                                     .order('-publication_date'))

  def get(self, format, tag):
    if format == 'json':
      return self.get_as_json(tag)
    elif format == 'db':
      return self.get_as_db(tag)

  def get_as_json(self, tag):
    query = self._get(tag)

    resources = []
    for r in query:
      second_author = None
      if r.second_author:
        second_author = r.second_author.key().name()
      resources.append(r.to_dict())
      resources[-1]['publication_date'] = r.publication_date.isoformat()
      resources[-1]['update_date'] = r.update_date.isoformat()
      resources[-1]['author'] = r.author.key().name()
      resources[-1]['second_author'] = second_author

    self.response.headers['Content-Type'] = 'application/json'
    self.response.out.write(simplejson.dumps(resources))
    #self.response.out.write(resources)
    return

  def get_as_db(self, tag):
    return self._get(tag)


def main():
  application = webapp.WSGIApplication([
    ('/api/(.*)', APIHandler),
    ('/tags/(.*)/(.*)', TagsHandler),
    ('/(.*)', ContentHandler)
  ], debug=True)
  run_wsgi_app(application)

if __name__ == '__main__':
  main()

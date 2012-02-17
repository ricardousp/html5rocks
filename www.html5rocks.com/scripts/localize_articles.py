#!/usr/bin/python
# Copyright 2011 Google Inc. All Rights Reserved.
# -*- coding: utf-8 -*-
#
# Licensed under the Apache License, Version 2.0 (the "License");
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

"""Generates HTML from Django, and Django from HTML for localization."""

from __future__ import with_statement

__author__ = ('mkwst@google.com (Mike West)')

import optparse
import os
import re

ROOT_DIR = os.path.abspath(os.path.dirname(__file__))

class TextProcessor(object):
  """Translates text from Django template format to localizable HTML and back."""
  
  UNTRANSLATABLE_BEGIN = r'<!--DO_NOT_TRANSLATE_BLOCK>'
  UNTRANSLATABLE_END = r'</DO_NOT_TRANSLATE_BLOCK-->'

  CONTENT_BEGIN = """
<!--CONTENT_BLOCK ***********************************************************-->
"""
  CONTENT_END = """
<!--/END_CONTENT_BLOCK ******************************************************-->
"""

  @classmethod
  def django_to_html(self, text):
    """Given a Django template's content, return HTML suitable for l10n.

    Args:
      text: The text to convert from Django to HTML.

    Returns:
      A string containing the newly HTMLized content.

      * Django tags like `{% tag %}` will be rendered inside an HTML comment:
        `<!--DO_NOT_TRANSLATE_BLOCK>{% tag %}</DO_NOT_TRANSLATE_BLOCK-->`.

      * `pre`, `script`, and `style` tags' content will be likewise wrapped:
        `<pre><!--DO_NOT_TRANSLATE_BLOCK>Text!</DO_NOT_TRANSLATE_BLOCK-->`.

      * The article's content will be wrapped:

        <!--CONTENT_BLOCK ***********************************************************-->
        Content goes here!
        <!--END_CONTENT_BLOCK *******************************************************-->
    """
    UNESCAPED_DJANGO = r'(?P<tag>{%.+?%})'
    ESCAPED_DJANGO = r'%s\g<tag>%s' % (self.UNTRANSLATABLE_BEGIN,
                                       self.UNTRANSLATABLE_END,)

    UNESCAPED_UNTRANSLATABLE = r'(?P<tag><(?:pre|script|style)[^>]*?>)'
    ESCAPED_UNTRANSLATABLE = r'\g<tag>%s' % self.UNTRANSLATABLE_BEGIN

    UNESCAPED_UNTRANSLATABLE_END = r'(?P<tag></(?:pre|script|style)[^>]*?>)'
    ESCAPED_UNTRANSLATABLE_END = r'%s\g<tag>' % self.UNTRANSLATABLE_END

    CONTENT_BEGIN = r'{% block content %}'
    CONTENT_END = r'{% endblock %}'
   
    # Walk the given text line by line
    to_return = []
    in_content = False
    for line in text.splitlines(True):
      # Process Django tags
      line = re.sub(UNESCAPED_DJANGO, ESCAPED_DJANGO, line)
      # Preprocess script/pre/style blocks
      line = re.sub(UNESCAPED_UNTRANSLATABLE, ESCAPED_UNTRANSLATABLE, line)
      line = re.sub(UNESCAPED_UNTRANSLATABLE_END,
                    ESCAPED_UNTRANSLATABLE_END,
                    line)
      # Preprocess content block
      if re.search(CONTENT_BEGIN, line):
        line = self.CONTENT_BEGIN
        in_content = True
      elif re.search(CONTENT_END, line) and in_content:
        line = self.CONTENT_END
        in_content = False

      to_return.append(line)

    return ''.join(to_return)

  @classmethod
  def html_to_django(self, text):
    """Given localized HTML, return text formatted as a Django template.

    Args:
      text: The text to convert from HTML to Django.

    Returns:
      A string containing the newly Djangoized content, stripped of leading
      and trailing whitespace.

      See the documentation for `django_to_html` and imagine the inverse. :)
    """
    # Strip UNTRANSLATABLE_BEGIN and UNTRANSLATABLE_END comments.
    text = text.replace(self.UNTRANSLATABLE_BEGIN, '')
    text = text.replace(self.UNTRANSLATABLE_END, '')

    # Replace CONTENT_BEGIN with `{% block content %}` and CONTENT_END with
    # `{% endblock %}`.
    text = text.replace(self.CONTENT_BEGIN, '{% block content %}\n')
    text = text.replace(self.CONTENT_END, '{% endblock %}')

    # Return the result, stripped of leading/training whitespace.
    return text.strip()

class ArticleException(Exception):
  pass

class Article(object):
  """Represents an article.
  
  Articles live at `{Article.ROOT}/path/to/article`, and can have one or more
  localizations, which live at `{Article.ROOT}/path/to/article/{locale}`. This
  class allows a mapping between a single article in three states:
  
  1. An article's finished localizations.
  2. An unlocalized representation of the article in HTML format.
  3. A newly localized instance of the article in a specific locale.

  Attributes:
    path: The path to the article's root: `{Article.ROOT}/path/to/article`.
    locales: The article's available completed localizations.
  """

  ROOT = os.path.abspath(os.path.join(ROOT_DIR, '..', 'content'))
  UNLOCALIZED_ROOT = os.path.abspath(os.path.join(ROOT_DIR, '..',
                                                  '_unlocalized'))
  PATH_DELIMITER = '___===___'

  def __init__(self, path):
    if not os.path.exists(path):
      raise ArticleException('`%s` does not exist.' % path)
    self.path = path
    self._locales = None

  @property
  def locales(self):
    """Returns the article's localizations.

    We assume that each of the subdirectories under |path| that contain an
    `index.html` file are localizations. This getter scans the filesystem
    to generate that list, then caches it for future calls.

    Returns:
      A list of locales, e.g. ['de','en','es']
    """
    if not self._locales:
      self._locales = [dir
                       for dir in os.listdir(self.path)
                       if os.path.exists(os.path.join(self.path, dir,
                                                      'index.html'))]
    return self._locales

  @property
  def localizable_file_path(self):
    """The path to an article's localizable representation.

    Unlocalized articles live under `{Article.UNLOCALIZED_ROOT}`, and are named
    by jamming the elements of the article's path together into one reversable
    string (the localization system doesn't like subdirectories).

    `{Article.ROOT}/path/to/article` would return
    `{Article.UNLOCALIZED_ROOT}/path__to__article.html` (where `__` represents
    `Article.PATH_DELIMITER`).

    Returns:
      An absolute path to the article's localizable representation.
    """
    temp = re.sub(r'^%s/' % self.ROOT, '', self.path)
    temp = re.sub(r'/', self.PATH_DELIMITER, temp)
    return os.path.abspath(os.path.join(self.UNLOCALIZED_ROOT, temp))

  def generate_localizable_file(self):
    """Generates a localizable representation of the article.

    This method grabs the English version of the article, runs the text through
    `TextProcessor.django_to_html`, and writes the result to
    `localizableFilePath`.

    Returns:
      An absolute path to the article's localizable representation.
    """
    if not 'en' in self.locales:
      raise ArticleException('`%s` has no English edition.' % self.path)

    original = os.path.join(self.ROOT, self.path, 'en', 'index.html')
    with open(original, 'r') as input:
      with open(self.localizable_file_path, 'w') as output:
        output.write(TextProcessor.django_to_html(input.read()))
    
    return self.localizable_file_path 

class Localizer(object):
  """Implements the HTML5Rocks article localization workflow.

  Given a root directory, Localizer looks for article files (defined as pretty
  much anything living directly under an `en` directory (which is very fragile
  and assumes we'll never have articles written first in languages other than
  English (which is probably accurate, but not something we should hard-code)))
  and converts the Django templates into HTML files that can be
  passed into a translation system (by commenting out all the Django bits).

  Localizer also goes in the other direction, converting localized HTML files
  into Django-template counterparts that can be rendered as part of HTML5Rocks.
  """

  def __init__(self, original_root=None, localized_root=None):
    """Constructs a Localizer object.

    Args:
      original_root: The directory which Localizer should scan for original
          articles.
      localized_root: The directory Localizer should scan for finished
          localizations.
    """
    self._original_root = original_root
    self._localized_root = localized_root
    self._articles = None

  def _index_english_articles(self):
    """Scans the original_root directory for English articles.

    Populates `self.articles_` with a list of Article objects representing
    each.

    Returns:
        list of Article objects
    """
    self._original_articles = []
    for root, _, files in os.walk(self._original_root):
      for name in files:
        if not name == '.DS_Store' and re.search(r'\/en$', root):
          self._original_articles.append(Article(os.path.dirname(root)))
    return self._original_articles

  @property
  def original_articles(self):
    if self._articles is None:
      self._index_english_articles()
    return self._original_articles

  def generate_localizable_files(self):
    for article in self.original_articles:
      article.generate_localizable_file()

# def main():
  # parser = optparse.OptionParser()
  # parser.add_option('--generate_html', dest='generate_html',
                    # default=False, action='store_true',
                    # help='Generate HTML from Django templated articles.')
  # parser.add_option('--generate_django', dest='generate_django',
                    # default=False, action='store_true',
                    # help=('Generate Django templates from localized '
                          # 'HTML articles.'))

  # options = parser.parse_args()[0]
  # if not options.generate_html or options.generate_django:
    # parser.error('You must specify either `--generate_html`'
                 # 'or `--generate_django`.')

  # l10n = Localizer(Article.DJANGO_ROOT)
  # if options.generate_html:
    # l10n.IndexEnglishArticles()
    # l10n.Htmlize()
  # if options.generate_django:
    # l10n.Djangoize()

if __name__ == '__main__':
  main()

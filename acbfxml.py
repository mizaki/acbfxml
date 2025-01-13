"""A class to encapsulate ACBF XML data."""
# Copyright 2012-2014 ComicTagger Authors
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
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from typing import Any
from typing import TYPE_CHECKING

from comicapi import utils
from comicapi._url import parse_url as parse_url
from comicapi.genericmetadata import GenericMetadata
from comicapi.genericmetadata import PageMetadata
from comicapi.tags import Tag

if TYPE_CHECKING:
    from comicapi.archivers import Archiver

logger = logging.getLogger(f'comicapi.metadata.{__name__}')


class ACBF(Tag):
    enabled = True

    id = 'acbf'

    def __init__(self, version: str) -> None:
        super().__init__(version)
        # Record the acbf versions we support
        self.namespaces = {'http://www.acbf.info/xml/acbf/1.1', 'http://www.acbf.info/xml/acbf/1.2'}

        self.file: str | None = None
        self.supported_attributes = {
            'series',
            'issue',
            'title',
            'volume',
            'genres',
            'description',
            'notes',
            'publisher',
            'imprint',
            'day',
            'month',
            'year',
            'language',
            'web_links',
            'manga',
            'maturity_rating',
            'scan_info',
            'tags',
            'pages',
            'pages.bookmark',
            'pages.image_index',
            'characters',
            'teams',
            'locations',
            'credits',
            'credits.person',
            'credits.role',
            'credits.language',
            'data_origin',
            'issue_id',
            'series_id',
            'identifier',  # ISBN
            'rights',  # License
        }

    def supports_credit_role(self, role: str) -> bool:
        return role.casefold() in self._get_parseable_credits()

    def supports_tags(self, archive: Archiver) -> bool:
        return archive.supports_files()

    def has_tags(self, archive: Archiver) -> bool:
        # Check for .acbf files
        self.file = None
        for file in archive.get_filename_list():
            if file.endswith('.acbf'):
                self.file = file
                break

        return (
            self.file is not None and self.supports_tags(archive) and self._validate_bytes(archive.read_file(self.file))
        )

    def remove_tags(self, archive: Archiver) -> bool:
        return self.has_tags(archive) and archive.remove_file(self.file)

    def read_tags(self, archive: Archiver) -> GenericMetadata:
        if self.has_tags(archive):
            metadata = archive.read_file(self.file) or b''
            if self._validate_bytes(metadata):
                return self._metadata_from_bytes(metadata, utils.get_page_name_list(archive.get_filename_list()))
        return GenericMetadata()

    def read_raw_tags(self, archive: Archiver) -> str:
        if self.has_tags(archive):
            return ET.tostring(ET.fromstring(archive.read_file(self.file)), encoding='unicode', xml_declaration=True)
        return ''

    def write_tags(self, metadata: GenericMetadata, archive: Archiver) -> bool:
        if self.supports_tags(archive):
            xml = b''
            if self.has_tags(archive):
                xml = archive.read_file(self.file)
            if self.file is None:
                self.file = 'comic_metadata.acbf'
            return archive.write_file(self.file, self._bytes_from_metadata(metadata, xml))
        logger.warning('Archive (%s) does not support %s metadata', archive.name(), self.name())
        return False

    def name(self) -> str:
        return 'ACBF'

    @classmethod
    def _get_parseable_credits(cls) -> list[str]:
        parsable_credits: list[str] = []
        parsable_credits.extend(GenericMetadata.writer_synonyms)
        parsable_credits.extend(GenericMetadata.penciller_synonyms)
        parsable_credits.extend(GenericMetadata.inker_synonyms)
        parsable_credits.extend(GenericMetadata.colorist_synonyms)
        parsable_credits.extend(GenericMetadata.letterer_synonyms)
        parsable_credits.extend(GenericMetadata.cover_synonyms)
        parsable_credits.extend(GenericMetadata.editor_synonyms)
        parsable_credits.extend(GenericMetadata.translator_synonyms)
        parsable_credits.append('adapter')
        parsable_credits.append('photographer')
        parsable_credits.append('assistant editor')
        parsable_credits.append('other')
        return parsable_credits

    def _metadata_from_bytes(self, string: bytes, file_list: list[str]) -> GenericMetadata:
        root = ET.fromstring(string)
        return self._convert_xml_to_metadata(root, file_list)

    def _bytes_from_metadata(self, metadata: GenericMetadata, xml: bytes = b'') -> bytes:
        root = self._convert_metadata_to_xml(metadata, xml)
        return ET.tostring(root, encoding='utf-8', xml_declaration=True)

    def _remove_acbf_xml_namespaces(self, root: ET.Element) -> None:
        # Remove all namespaces because it's too complicated otherwise.
        # This can cause issues if someone decides to actually use namespaces when writing an acbf file.
        # This shouldn't matter as the official ACBF editor does the same thing
        for ele in root.iter():
            if ele.tag.startswith('{'):
                ele.tag = ele.tag.split('}')[1]

    def _convert_metadata_to_xml(self, metadata: GenericMetadata, xml: bytes = b'') -> ET.Element:
        def add_element(element: ET.Element, sub_element: str, text: str = '', attribs: dict[str, str] | None = None) -> None:
            if not isinstance(element, ET.Element):
                raise Exception('add_element: Not an ET.Element: %s', element)

            attribs = attribs or {}

            new_element = ET.SubElement(element, sub_element)

            if text:
                new_element.text = str(text)

            for k, v in attribs.items():
                new_element.attrib[k] = v

        def add_path(path: str) -> ET.Element:
            path_list: list[str] = path.split('/')
            test_path: str = ''

            for i, p in enumerate(path_list):
                test_path = '/'.join(path_list[:i + 1])

                if root.find(test_path) is None:
                    if i == 0:
                        add_element(root, p)
                    else:
                        *element_path_parts, element_name = test_path.split('/')
                        element_path = '/'.join(element_path_parts)
                        add_root = root.find(element_path)
                        if add_root is None:
                            raise Exception('add_path: Failed to find XML path element: %s', add_root)
                        else:
                            add_element(add_root, p)

            ele = root.find(path)
            if ele is None:
                raise Exception('add_path: Failed to create XML path element: %s', path)
            else:
                return ele

        def get_or_create_element(tag: str) -> ET.Element:
            element = root.find(tag)
            if element is None:
                element = add_path(tag)
            return element

        def remove_attribs(ele: ET.Element) -> ET.Element:
            ele.attrib.clear()
            return ele

        def modify_element(path: str, value: Any, attribs: dict[str, str] | None = None, clear_attribs: bool = False) -> None:
            attribs = attribs or {}

            # Split the path into parent and element name
            *element_path_parts, element_name = path.split('/')
            element_path = '/'.join(element_path_parts)

            element_parent = get_or_create_element(element_path)

            element = root.find(path)
            if element is None:
                try:
                    element = ET.SubElement(element_parent, element_name)
                except Exception as e:
                    logger.warning(f'Failed to modify XML element: {element_path}, {element_name}. Error: {e}')
                    return

            if clear_attribs:
                element = remove_attribs(element)

            element.text = str(value)
            for k, v in attribs.items():
                element.attrib[k] = v

        def clear_element(full_ele: str) -> None:
            element_path, _, element_name = full_ele.rpartition('/')
            element_parent = root.find(element_path)
            if element_parent is not None:
                for e in element_parent.findall(element_name):
                    element_parent.remove(e)

        def add_page(md_page: PageMetadata, xml_page: ET.Element | None = None, is_cover: bool = False) -> None:
            if xml_page is not None:
                if md_page.bookmark:
                    for t in xml_page.findall('title'):
                        # Empty lang is presumed 'en', remove any to add new bookmark/title
                        if t.get('lang', '') in ['en', '']:
                            xml_page.remove(t)
            else:
                xml_page = ET.Element('page')
                add_element(xml_page, 'image', '', {'href': page.filename})

            if md_page.bookmark:
                if md.language:
                    add_element(xml_page, 'title', md_page.bookmark, {'lang': md.language})
                else:
                    add_element(xml_page, 'title', md_page.bookmark)

            if is_cover:
                xml_page.tag = 'coverpage'
                book_info.append(xml_page)
            else:
                body_node.append(xml_page)

        def add_credit(person: str, role: str, lang: str | None = None) -> None:
            # There is no way to know first from last from Credit.person so assume first last by spaces
            first: str | None = None
            middle: str | None = None
            last: str | None = None
            nick: str | None = None

            name_split = person.split()
            if len(name_split) == 1:
                nick = name_split[0]
            elif len(name_split) == 2:
                first = name_split[0]
                last = name_split[1]
            elif len(name_split) > 2:
                first = name_split[0]
                middle = name_split[1]
                last = name_split[2]

            if not (first or last or nick):
                return

            element = ET.SubElement(book_info, 'author', activity=role)
            if lang is not None:
                element.attrib['lang'] = lang
            if first is not None:
                add_element(element, 'first-name', first)
            if middle is not None:
                add_element(element, 'middle-name', middle)
            if last is not None:
                add_element(element, 'last-name', last)
            if nick is not None:
                add_element(element, 'nickname', nick)

        # xml is empty bytes or has the read acbf xml
        # shorthand for the metadata
        md = metadata
        ns_url = 'http://www.acbf.info/xml/acbf/1.2'
        if xml:
            root: ET.Element = ET.fromstring(xml)
            self._remove_acbf_xml_namespaces(root)
            root.attrib['xmlns'] = ns_url
        else:
            # build a tree structure
            root = ET.Element('ACBF')
            root.attrib['xmlns'] = ns_url

        book_info = get_or_create_element('meta-data/book-info')

        # Comic authors
        # 'Writer', 'Adapter', 'Artist', 'Penciller', 'Inker', 'Colorist', 'Letterer', 'CoverArtist', 'Photographer',
        # 'Editor', 'Assistant Editor', 'Translator', 'Other'
        # Wipe all authors as any from the XML should be in md
        # TODO Need to dedupe?
        clear_element('meta-data/book-info/author')

        for credit in md.credits:
            if credit.role.casefold() in GenericMetadata.writer_synonyms:
                add_credit(credit.person, 'Writer', credit.language)

            elif credit.role.casefold() in ['adapter']:
                add_credit(credit.person, 'Adapter', credit.language)

            elif credit.role.casefold() in ['artist']:
                add_credit(credit.person, 'Artist')

            elif credit.role.casefold() in GenericMetadata.penciller_synonyms:
                add_credit(credit.person, 'Penciller')

            elif credit.role.casefold() in GenericMetadata.inker_synonyms:
                add_credit(credit.person, 'Inker')

            elif credit.role.casefold() in GenericMetadata.colorist_synonyms:
                add_credit(credit.person, 'Colorist')

            elif credit.role.casefold() in ['photographer', 'photo']:
                add_credit(credit.person, 'Photographer')

            elif credit.role.casefold() in GenericMetadata.letterer_synonyms:
                add_credit(credit.person, 'Letterer', credit.language)

            elif credit.role.casefold() in GenericMetadata.cover_synonyms:
                add_credit(credit.person, 'CoverArtist')

            elif credit.role.casefold() in GenericMetadata.editor_synonyms:
                add_credit(credit.person, 'Editor', credit.language)

            elif credit.role.casefold() in ['assistant editor']:
                add_credit(credit.person, 'Assistant Editor', credit.language)

            elif credit.role.casefold() in GenericMetadata.translator_synonyms:
                add_credit(credit.person, 'Translator', credit.language)

            else:
                add_credit(credit.person, 'Other', credit.language)

        if md.series:
            sequence = root.findall('meta-data/book-info/sequence')
            # If there is only one sequence field, replace it. Otherwise, keep all but dupe issue number
            if len(sequence) == 1:
                sequence.clear()
            else:
                for seq in sequence:
                    # Will presume if the number is the same as md, can be removed and re-added with updated data
                    if seq.text == md.issue:
                        book_info.remove(seq)

            element = ET.SubElement(book_info, 'sequence')
            element.attrib['title'] = md.series
            if md.issue:
                element.text = md.issue
            if md.volume:
                element.attrib['volume'] = str(md.volume)

        if md.title:
            cur_titles: list[ET.Element] = root.findall('meta-data/book-info/book-title')
            found = False
            # Clear any 'en' or no language field to be replaced with new
            for title in cur_titles:
                if title.attrib.get('lang') is None or title.attrib.get('lang') == 'en':
                    title.clear()

            element = ET.SubElement(book_info, 'book-title')
            element.text = md.title
            if md.language:
                element.attrib['lang'] = md.language

        allow_genres = [
            'other', 'adult', 'adventure', 'alternative', 'artbook', 'biography', 'caricature', 'children',
            'computer', 'crime', 'education', 'fantasy', 'history', 'horror', 'humor', 'manga', 'military',
            'mystery', 'non-fiction', 'politics', 'real_life', 'religion', 'romance', 'science_fiction',
            'sports', 'superhero', 'western',
        ]
        # Store current genres for 'match' values
        cur_genres: list[ET.Element] = root.findall('meta-data/book-info/genre')
        clear_element('meta-data/book-info/genre')
        if md.manga is not None and md.manga.casefold().startswith('yes'):
            md.genres.add('manga')
        for g in md.genres:
            g = g.casefold().replace(' ', '_')
            # TODO More replacements?
            if g == 'historical':
                g = 'history'

            if g in allow_genres:
                # Check for current to keep any match value
                match: int = 0
                for cg in cur_genres:
                    if cg.text == g:
                        if cg.get('match'):
                            match = int(cg.attrib['match'])
                        break
                if match > 0:
                    add_element(book_info, 'genre', g, {'match': str(match)})
                else:
                    add_element(book_info, 'genre', g)

        if md.description:
            cur_annos: list[ET.Element] = root.findall('meta-data/book-info/annotation')
            found = False
            for anno in cur_annos:
                # An annotation should have <p> tags
                if len(anno) > 0:
                    split_a = [a.text for a in anno]
                    split_desc = md.description.split('\n\n')
                    if len(split_a) == len(split_desc):
                        # Same number of paragraphs, so check text
                        inter_found = 0
                        for i, text in enumerate(split_desc):
                            if text == split_a[i]:
                                inter_found += 1

                        if inter_found == len(split_desc):
                            found = True
                            break
                # Possible improperly formatted, missing p tags
                elif anno.text == md.description:
                    found = True
                    break
            if not found:
                element = ET.SubElement(book_info, 'annotation')
                text_list = md.description.split('\n\n')
                for t in text_list:
                    add_element(element, 'p', t)
                if md.language:
                    for a in root.findall('meta-data/book-info/annotation'):
                        if a.get('lang') == md.language:
                            # Remove current annotation with same language attrib
                            book_info.remove(a)
                            break
                    element.attrib['lang'] = md.language

        dbname: str = 'Unknown' if md.data_origin is None else md.data_origin.name
        if md.web_links:
            for dbref in book_info.findall('databaseref'):
                if dbref.get('type', '').casefold() == 'url':
                    book_info.remove(dbref)
            for web in md.web_links:
                add_element(book_info, 'databaseref', web.url, {'type': 'URL', 'dbname': dbname})

        if md.maturity_rating:
            found = False
            for rate in book_info.findall('content-rating'):
                if rate.text == md.maturity_rating:
                    found = True
                    break
            if not found:
                add_element(book_info, 'content-rating', md.maturity_rating)

        if md.tags:
            modify_element('meta-data/book-info/keywords', ', '.join(md.tags))

        if md.characters:
            chars = get_or_create_element('meta-data/book-info/characters')
            chars.clear()
            for c in md.characters:
                add_element(chars, 'name', c)

        if md.teams:
            teams = get_or_create_element('meta-data/book-info/teams')
            teams.clear()
            for team in md.teams:
                add_element(teams, 'name', team)

        if md.locations:
            locs = get_or_create_element('meta-data/book-info/locations')
            locs.clear()
            for loc in md.locations:
                add_element(locs, 'name', loc)

        if md.issue_id or md.series_id:
            add_issue: bool = True
            add_series: bool = True
            for dbref in book_info.findall('databaseref'):
                if dbref.get('type', '').casefold() in ['issueid', 'issue_id', 'issue-id']:
                    if md.issue_id is not None and dbref.text == md.issue_id:
                        # Could check 'dbname' too but chances of colliding IDs from different sources seems small
                        add_issue = False

                    if dbref.get('type', '').casefold() in ['seriesid', 'series_id', 'series-id']:
                        if md.series_id is not None and dbref.text == md.series_id:
                            add_series = False
            if md.issue_id and add_issue:
                add_element(book_info, 'databaseref', md.issue_id, {'type': 'IssueID', 'dbname': dbname})
            if md.series_id and add_series:
                add_element(book_info, 'databaseref', md.series_id, {'type': 'SeriesID', 'dbname': dbname})

        # publisher-info

        get_or_create_element('meta-data/publish-info')

        if md.identifier:
            modify_element('meta-data/publish-info/isbn', md.identifier)

        if md.publisher:
            if md.imprint:
                modify_element('meta-data/publish-info/publisher', md.publisher, {'imprint': md.imprint})
            else:
                modify_element('meta-data/publish-info/publisher', md.publisher, clear_attribs=True)

        else:
            clear_element('meta-data/publish-info/publisher')

        if md.year:
            day = md.day or 1
            month = md.month or 1
            year = md.year
            if int(year) < 50:
                # Presume 20xx
                year = 2000 + year
            elif year < 100:
                year = 1900 + year

            pub_date = f'{year:04}-{month:02}-{day:02}'
            modify_element('meta-data/publish-info/publish-date', pub_date, {'value': pub_date})

        # document-info

        if md.notes:
            history = get_or_create_element('meta-data/document-info/history')
            notes_split = md.notes.split('\n')
            history.clear()
            for n in notes_split:
                add_element(history, 'p', n)

        if md.scan_info:
            source = get_or_create_element('meta-data/document-info/source')
            for s in source:
                if s.text and s.text.startswith('[Scan]'):
                    source.remove(s)

            add_element(source, 'p', f'[Scan]{md.scan_info}')

        #  loop and add the page entries under pages node
        body_node = get_or_create_element('body')
        # Create a dict for current page data
        page_dict: dict[str, ET.Element] = {}

        # Cover page is separate for reasons...
        coverpage = book_info.find('coverpage')
        if coverpage is not None:
            image = coverpage.find('image')
            if image is not None:
                href = image.get('href')
                if href is not None:
                    # Change tag from 'coverpage' to 'page' for ease later
                    coverpage.tag = 'page'
                    page_dict[href] = coverpage

            book_info.remove(coverpage)

        for b in body_node:
            # There should only be page tags but we'll verify
            if b.tag == 'page':
                image = b.find('image')
                if image is not None:
                    href = image.get('href')
                    if href is not None:
                        page_dict[href] = b

        # Save the body attributes so we keep the default background color
        body_attrib = body_node.attrib.copy()
        body_node.clear()
        body_node.attrib = body_attrib

        # pages will be in file name order, not page list order
        md.pages = sorted(md.pages, key=lambda x: x.display_index)

        for i, page in enumerate(md.pages):
            old_xml_page = page_dict.get(page.filename)
            is_cover: bool = False
            # Cover page lives in book-info, not body
            if i == 0:
                is_cover = True
            if old_xml_page is None:
                add_page(page, None, is_cover)
            else:
                add_page(page, old_xml_page, is_cover)

        ET.indent(root)

        return root

    def _convert_xml_to_metadata(self, root: ET.Element, file_list: list[str]) -> GenericMetadata:

        def get(name: str) -> str | None:
            tag = root.find('.//' + name)
            if tag is None:
                return None
            return tag.text

        def get_with_lang(name: str, lang: str = 'en') -> str | None:
            # lang attrib is optional
            if book_info is not None:
                tags = book_info.findall(name)
                if len(tags) == 0:
                    return None

                for tag in tags:
                    if tag.get('lang') is None or tag.get('lang') == lang:
                        return tag.text

            return None

        def annotation_to_string(ele: ET.Element) -> str | None:
            anno: list[str] = []
            # annotation - may have lang attrib and *should* have <p> children but will check
            if len(ele) > 0:
                for a in ele:
                    if a.text:
                        anno.append(a.text)
            else:
                if ele.text:
                    anno.append(ele.text)

            if len(anno) > 0:
                return '\n\n'.join(anno)

            return None

        # We only allow using the specific versions we know we are compatible with
        acbf_tags = {f'{{{ns}}}ACBF' for ns in self.namespaces}
        acbf_tags.add('ACBF')

        if str(root.tag) not in acbf_tags:
            if root.tag.endswith('}ACBF'):
                raise Exception('Unknown ACBF version: ' + str(root.tag).removesuffix('ACBF').strip('{}'))
            raise Exception('Not an ACBF file')
        self._remove_acbf_xml_namespaces(root)

        md = GenericMetadata()

        book_info = root.find('meta-data/book-info')

        if book_info is None:
            logger.info('No metadata found in ACBF file')
            return md

        seq = book_info.findall('sequence')
        if len(seq) > 0:
            # Use first item
            md.series = seq[0].get('title')
            md.volume = seq[0].get('volume')
            md.issue = seq[0].text or None

        md.title = utils.xlate(get_with_lang('book-title'))

        # Not super clear what's supposed to be what, but we always want a series title
        if md.series is None:
            md.series = md.title
            md.title = None

        for g in book_info.findall('genre'):
            if g.text:
                if g.text.casefold() == 'manga':
                    md.manga = 'Yes'
                md.genres.add(g.text.replace('_', ' ').casefold())

        anno = book_info.findall('annotation')
        for d in anno:
            # Multiple languages, priority is lang attrib: None (missing)->en->whatever is found
            lang = d.get('lang', '')
            if lang == '':
                md.description = annotation_to_string(d)
                break
            elif lang == 'en':
                md.description = annotation_to_string(d)
            elif md.description is None:
                md.description = annotation_to_string(d)

        publisher = root.find('.//publisher')
        if publisher is not None:
            md.publisher = utils.xlate(publisher.text)
            md.imprint = publisher.get('imprint')

        # Parse date. The `value` field is ISO but the `text` is anything
        pub_date = root.find('.//publish-date')
        if pub_date is not None:
            md.day, md.month, md.year = utils.parse_date_str(pub_date.get('value'))

        if md.year is None and pub_date and pub_date.text:
            # Try to parse a year to aid tagging
            match = re.match(r'\d{4}', pub_date.text)
            md.year = match[0] if match is not None else None

        langs = book_info.findall('languages')
        if len(langs) > 0:
            md.language = langs[0][0].get('lang')  # Take first for now

        md.maturity_rating = utils.xlate(get('content-rating'))

        md.tags = set(utils.split(get('keywords'), ','))

        for c in book_info.findall('characters/name'):
            md.characters.add(c.text)

        for t in book_info.findall('teams/name'):
            md.teams.add(t.text)

        for loc in book_info.findall('locations/name'):
            md.locations.add(loc.text)

        for dbrefs in book_info.findall('databaseref'):
            dbtype = dbrefs.get('type')
            if dbtype is not None:
                # Can't use IssueID or SeriesID realistically
                if dbtype.casefold() == 'url':
                    md.web_links.append(parse_url(dbrefs.text))

        md.identifier = utils.xlate(get('isbn'))

        # Now extract the credit info
        for n in book_info.findall('author'):
            name: str = ''
            first = n.find('first-name')
            middle = n.find('middle-name')
            last = n.find('last-name')
            nick = n.find('nickname')
            role = n.get('activity')
            language = n.get('lang', '')

            if role:
                if role.casefold() == 'coverartist':
                    role = 'Cover'

                if first is not None and last is not None and first.text and last.text:
                    if middle is None:
                        name = first.text + ' ' + last.text
                    elif middle.text:
                        name = first.text + ' ' + middle.text + ' ' + last.text
                elif nick is not None and nick.text:
                    name = nick.text
                elif first is not None and first.text:
                    name = first.text
                else:
                    continue

                md.add_credit(name, role, False, language)

        # history to notes
        history = root.find('.//history')
        if history:
            hist_list: list[str] = []
            for h in history:
                if h.text:
                    hist_list.append(h.text)
            md.notes = '\n'.join(hist_list)

        # source (label scan info)
        source = root.find('.//source')
        if source:
            for s in source:
                if s.text and s.text.startswith('[Scan]'):
                    md.scan_info = s.text[6:]

        # parse page data now
        pages_node = root.findall('body/page')

        page_file_list: dict[str, int] = {}
        for i, f in enumerate(file_list):
            page_file_list[f] = i

        # Cover page is separate for reasons...
        coverpage = book_info.find('coverpage')
        if coverpage is not None:
            pages_node.insert(0, coverpage)

        for i, page in enumerate(pages_node):
            image = page.find('image')
            titles = page.findall('title')
            title = ''
            for t in titles:
                lang = t.get('lang', '')
                # Multiple languages, priority is lang attrib: None (missing), en, whatever is found
                if lang == '' and t.text:
                    title = t.text
                    break
                elif lang == 'en' and t.text:
                    title = t.text
                elif title == '' and t.text:
                    title = t.text

            filename = image.get('href', '') if image is not None else ''
            # Matching the archive_index here _is_ necessary as _currently_ it's what links the page to the rest of the data.
            # It should change to be archive_index or filename in the future
            archive_index = page_file_list.get(filename)
            if archive_index is None:
                archive_index = i

            md_page = PageMetadata(
                filename=filename,
                display_index=i,
                archive_index=archive_index,
                bookmark=title,
                type='',
            )

            md.pages.append(md_page)

        md.is_empty = False

        return md

    def _validate_bytes(self, string: bytes) -> bool:
        """Verify that the string actually contains ACBF data in XML format."""
        try:
            root = ET.fromstring(string)
            if not root.tag.endswith('ACBF'):
                return False
        except ET.ParseError:
            return False

        return True

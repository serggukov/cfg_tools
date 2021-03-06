# -*- coding: utf-8 -*-
import cfg_tools.reader_1cd as reader_1cd
import os
from cfg_tools import utils
import xml.etree.ElementTree as etree
from cfg_tools.common import Ref
from cfg_tools import reader_cf
import io
import logging
from struct import unpack
from cfg_tools import common
import binascii

logger = None


def rmdir_r(path):
    if not os.path.exists(path):
        return
    for name in os.listdir(path):
        file = os.path.join(path, name)
        if not os.path.islink(file) and os.path.isdir(file):
            rmdir_r(file)
        else:
            os.remove(file)
    os.rmdir(path)


class User(common.Ref):

    def __init__(self, data, name, email=None):
        super(User, self).__init__(data, name)
        self.email = email if email is not None else self.name + '@localhost'
        self.git_name = self.name


class MetaObject(common.Ref):

    def __init__(self, data):
        super(MetaObject, self).__init__(data, None)
        self.meta_class = None
        self.parent = None
        self.removed = False
        self.files = []


class Depot83Reader:

    def __init__(self, path):
        self.path = path
        self.files = []
        self.init()

    def init(self):
        self.files.clear()
        ind_files = []
        pack_files = []
        for root, dirs, files in os.walk(os.path.join(self.path, 'pack')):
            for file in files:
                if file.endswith(".ind"):
                    ind_files.append(os.path.join(root, file))
                elif file.endswith('.pck'):
                    pack_files.append(os.path.join(root, file))
        for file in ind_files:
            with open(file, 'rb') as stream:
                sub_files = {}
                self.files.append({
                    'name': file[:-4] + '.pck',
                    'files': sub_files
                })
                sign = unpack('4s4sI', stream.read(12))
                count = sign[2]
                for i in range(count):
                    data = stream.read(28)
                    sub_files[binascii.hexlify(data[:20]).decode()] = unpack('q', data[20:])[0]
                    if not data:
                        break
                stream.close()

    def get_file(self, hash_name):
        for file in self.files:
            if hash_name in file['files']:
                with open(file['name'], 'rb') as stream:
                    stream.seek(file['files'][hash_name])
                    size = unpack('q', stream.read(8))[0]
                    data = stream.read(size)
                    stream.close()
                    return data
        source = os.path.join(os.path.join(self.path, 'objects'), hash_name[:2], hash_name[2:])
        with open(source, 'rb') as stream:
            data = stream.read()
            stream.close()
            return data


class StoreReader(reader_1cd.Reader1CD):

    @staticmethod
    def _write_file(data, file_name):
        with open(file_name, 'wb+') as f:
            f.write(data)
            f.close()

    def __init__(self, file):
        super(StoreReader, self).__init__(file)
        self.users = None
        self.versions = None
        self.meta_classes = None
        self.format_83 = True
        self.root_uid = None
        self.objects_info = None
        self.depot83_files_reader = None
        self.read()

    def _read_objects(self):
        if self.objects_info is not None:
            for obj in self.objects_info.values():
                obj.files.clear()
            return
        self.objects_info = {}
        for row in self.read_table_by_name('OBJECTS', push_headers=False):
            obj_id = row.by_name('OBJID')
            obj = MetaObject(obj_id)
            obj.meta_class = self.meta_classes[row.by_name('CLASSID')] if row.by_name('CLASSID') in self.meta_classes else row.by_name('CLASSID')

            if not self.format_83:
                obj.parent = row.by_name('PARENTID')
            self.objects_info[obj_id] = obj
        if not self.format_83:
            self._set_parents()

    def _set_parents(self):
        for obj in self.objects_info.values():
            if isinstance(obj.parent, common.Guid) and \
               obj.parent != common.Guid.EMPTY and \
               obj.parent != self.root_uid and \
               obj.parent in self.objects_info:
                obj.parent = self.objects_info[obj.parent]
            else:
                obj.parent = None

    def _get_objects_by_version(self, version_number):
        objects = {}
        row_version = 0
        for row in self.read_table_by_name('HISTORY',
                                           push_headers=False):
            assert row_version <= row.by_name('VERNUM')
            row_version = row.by_name('VERNUM')

            obj_id = row.by_name('OBJID')
            obj = self.objects_info[obj_id]
            obj.name = row.by_name('OBJNAME')
            if self.format_83:
                obj.parent = row.by_name('PARENTID')

            if row_version > version_number:
                break
            elif row_version != version_number:
                continue
            obj.files.clear()

            objects[obj_id] = obj
            obj.removed = row.by_name('REMOVED')
            obj.files.append({
                'data': row.by_name('DATAHASH') if self.format_83 else row.get_blob('OBJDATA'),
                'packed': row.by_name('DATAPACKED'),
                'name': 'info.txt'
            })
        if self.format_83:
            self._set_parents()
        gen = self.read_table_by_name('EXTERNALS',
                                      push_headers=False,
                                      read_blob=False)

        row_version = 0
        for row in gen:
            assert row_version <= row.by_name('VERNUM')
            row_version = row.by_name('VERNUM')
            if row_version > version_number:
                break
            elif row_version != version_number:
                continue

            obj_id = row.by_name('OBJID')
            if obj_id not in objects:
                logger.error('Найден файл не принадлежащий объекту. OBJID: %s; EXTNAME: %s' %
                             (row.by_name('OBJID'), row.by_name('EXTNAME')))
            elif row.by_name('EXTVERID') == common.Guid.EMPTY:
                logger.debug('Пропущен файл: %s' % row.by_name('EXTNAME'))
            else:
                objects[obj_id].files.append(
                    {
                        'name': row.by_name('EXTNAME'),
                        'data': row.by_name('DATAHASH') if self.format_83 else row.get_blob('EXTDATA'),
                        'packed': row.by_name('DATAPACKED')
                    })

        logger.debug('version objects (%s) %s' % (len(objects), ', '.join([item.name for item in objects.values()])))
        return [v for v in objects.values()]

    def _read_objects_by_version(self, start_version, last_version=None):
        """
        Генератор, возвращает список объектов и файлов для каждой версии хранилища
        :param int start_version: Начальная версия
        :param int last_version: Последная версия
        :return tuple(int, list): Кортеж: номер версии, объекты версии
        """
        history_iter = self.read_table_by_name('HISTORY',
                                               push_headers=False)
        externals_iter = self.read_table_by_name('EXTERNALS',
                                                 push_headers=False,
                                                 read_blob=False)

        # move to start_version
        history_row = None
        external_row = None
        for row in history_iter:
            obj_id = row.by_name('OBJID')
            obj = self.objects_info[obj_id]
            obj.name = row.by_name('OBJNAME')
            obj.removed = row.by_name('REMOVED')
            if self.format_83:
                obj.parent = row.by_name('PARENTID')
            if row.by_name('VERNUM') >= start_version:
                history_row = row
                break

        for row in externals_iter:
            if row.by_name('VERNUM') >= start_version:
                external_row = row
                break
        if history_row is None or external_row is None or history_row.by_name('VERNUM') < start_version:
            return None
        current_version = history_row.by_name('VERNUM')
        while True:  # Основной цикл по версиям
            objects = {}
            # Собираем данные об выгружаемых объектах
            while history_row and current_version == history_row.by_name('VERNUM'):
                obj = self.objects_info[history_row.by_name('OBJID')]
                obj.name = history_row.by_name('OBJNAME')
                obj.removed = history_row.by_name('REMOVED')
                if self.format_83:
                    obj.parent = history_row.by_name('PARENTID')
                obj.files.clear()
                obj.files.append({
                    'data': history_row.by_name('DATAHASH') if self.format_83 else history_row.get_blob('OBJDATA'),
                    'packed': history_row.by_name('DATAPACKED'),
                    'name': 'info.txt',
                })
                objects[history_row.by_name('OBJID')] = obj
                try:
                    history_row = next(history_iter)
                except StopIteration:
                    history_row = None
                    break
            if self.format_83:
                # Проставим свяжем родителей по uid
                self._set_parents()
            # Соберем данные о доп. файлах объектов(модули, справка, предопределенные и тд)
            while external_row and current_version == external_row.by_name('VERNUM'):
                obj_id = external_row.by_name('OBJID')
                if obj_id not in objects:
                    logger.error('Найден файл не принадлежащий объекту. OBJID: %s; EXTNAME: %s' %
                                 (external_row.by_name('OBJID'), external_row.by_name('EXTNAME')))
                elif external_row.by_name('EXTVERID') == common.Guid.EMPTY:
                    logger.debug('Пропущен файл: %s. Инфо: %s' % (external_row.by_name('EXTNAME'), external_row))
                else:
                    objects[obj_id].files.append(
                        {
                            'name': external_row.by_name('EXTNAME'),
                            'data': external_row.by_name('DATAHASH') if self.format_83 else external_row.get_blob('EXTDATA'),
                            'packed': external_row.by_name('DATAPACKED')
                        })
                try:
                    external_row = next(externals_iter)
                except StopIteration:
                    external_row = None
                    break
            yield current_version, [v for v in objects.values()]
            if history_row is None and external_row is None:
                break
            current_version = min(history_row.by_name('VERNUM'), external_row.by_name('VERNUM'))
            if (last_version and current_version > last_version):
                break

    def _load_classes(self):
        if self.meta_classes:
            return
        tree = etree.parse(os.path.join(os.path.dirname(__file__), 'classID.xml'))
        file_groups = {
            group.attrib['name']: {
                                    file.attrib['id']: (file.attrib['name'], file.attrib['content_type'])
                                    for file in group.getiterator('file')}
            for group in tree.getiterator('type')
        }
        self.meta_classes = {}
        for cls in tree.getiterator('class'):
            meta_class = Ref(
                utils.guid_to_bytes(cls.attrib['id']),
                cls.attrib['single'])
            meta_class.type = cls.attrib['type'] if 'type' in cls.attrib else None
            meta_class.multiple = cls.attrib['multiple'] if 'multiple' in cls.attrib else meta_class.name

            meta_class.files = {
                file.attrib['id']: (file.attrib['name'], file.attrib['content_type'])
                for file in cls.getiterator('file')
                }
            if len(meta_class.files) == 0 and meta_class.type is not None and meta_class.type in file_groups:
                meta_class.files = file_groups[meta_class.type]
            self.meta_classes[utils.guid_to_bytes(cls.attrib['id'])] = meta_class

    def _unpuck_file(self, data, name, meta_class):
        if meta_class and '.' in name and name[name.rindex('.'):] in meta_class.files:
            content_type = meta_class.files[name[name.rindex('.'):]][1]
            name = meta_class.files[name[name.rindex('.'):]][0]
            if content_type == 'module':
                ext = '.txt'
            elif content_type == 'xml':
                ext = '.xml'
            else:
                ext = '.mxl'
        else:
            ext = ''

        if data[:4] == reader_cf.bytes7fffffff:
            cf_files = reader_cf.ReaderCF.read_container(io.BytesIO(data))
            for file_name in cf_files:
                if file_name == 'info':
                    continue
                if file_name == 'form':
                    yield 'Форма.mxl', cf_files[file_name]
                elif file_name == 'module':
                    yield 'Модуль.txt', cf_files[file_name]
                elif file_name == 'text':
                    yield name + ext, cf_files[file_name]
                elif file_name == 'image':
                    yield name + '_СкомпилированныйОбраз' + ext, cf_files[file_name]
                else:
                    yield name + '_' + file_name + ext, cf_files[file_name]

        else:
            yield name + ext, data

    def _save_files(self, objects, path, hierarchy=True):
        files = []
        for obj in objects:
            meta_class = obj.meta_class
            str_guid = str(obj.data)
            full_name = ''
            parent = obj
            while 1:
                full_name = os.path.sep.join([str(parent.meta_class.multiple), parent.name, full_name]) \
                            if hierarchy else \
                            '.'.join([str(parent.meta_class.name), parent.name, full_name])
                parent = parent.parent
                if parent is None:
                    break
            obj_path = os.path.join(path, full_name)
            if obj.removed:
                logger.debug('Remove %s' % full_name)
                rmdir_r(obj_path)
                continue
            logger.debug('Export %s' % full_name)
            if hierarchy and not os.path.exists(obj_path):
                os.makedirs(obj_path)
            for file_info in obj.files:
                if file_info['data'] is None:
                    continue
                data = self.depot83_files_reader.get_file(file_info['data']) if self.format_83 else file_info['data']
                if data is None:
                    continue
                if file_info['packed']:
                    data = utils.inflate_inmemory(data)
                for name, data in self._unpuck_file(data,
                                                    file_info['name'],
                                                    meta_class
                                                    if obj == self.root_uid or file_info['name'][:36] == str_guid
                                                    else None):
                    self._write_file(data, os.path.join(obj_path, name))
                    files.append(os.path.join(obj_path, name))

        logger.debug('Saved %s files' % len(files))
        return files

    def read(self):
        super(StoreReader, self).read()
        self.format_83 = 'DATAHASH' in self.get_table_info('HISTORY').fields_indexes
        for row in self.read_table_by_name('DEPOT'):
            self.root_uid = row.by_name('ROOTOBJID')
        if self.format_83:
            self.depot83_files_reader = Depot83Reader(os.path.join(os.path.dirname(self.file_name), 'data'))

    def read_users(self):
        if not self.users:
            gen = self.read_table_by_name('USERS',
                                          read_blob=True,
                                          push_headers=False)
            self.users = {row.by_name('USERID'): User(row.by_name('USERID'), row.by_name('NAME')) for row in gen}

    def read_versions(self):
        self.read_users()
        if not self.versions:
            gen = self.read_table_by_name('VERSIONS',
                                          read_blob=True,
                                          push_headers=False)
            self.versions = {row[0]: {
                'verion': row.by_name('VERNUM'),
                'user': self.users[row.by_name('USERID')],
                'comment': row.by_name('COMMENT'),
                'date': row.by_name('VERDATE')
            } for row in gen}

    def export_version(self, version_number, path, hierarchy=True):
        """
        Выгрузка версии хранилища, при множественной выгрузке лучше использовать соответствующую функцию
        :param int version_number: Номер выгружаемой версии
        :param str path: Каталог сохранения файлов
        :param bool hierarchy: Иерархическая выгрузка(по каталогам)
        :return list: выгруженные файлы
        """
        self._load_classes()
        self._read_objects()

        objects = self._get_objects_by_version(version_number)
        return self._save_files(objects, path, hierarchy)

    def export_versions(self, path, start_version, last_version=None, hierarchy=True):
        """
        Оптимизированная выгрузка нескольких версий
        :param str path: Каталог сохранения файлов
        :param int start_version: Начальная версия хранилища(включительно)
        :param int last_version: Последная выгружаемая версия(включительно)
        :param bool hierarchy: Иерархическая выгрузка(по каталогам)
        :return tuple(int, list): Кортеж: номер версии, выгруженные файлы
        """
        self._load_classes()
        self._read_objects()
        for objects in self._read_objects_by_version(start_version, last_version):
            logger.info('Exporting version: %s' % objects[0])
            files = self._save_files(objects[1], path, hierarchy)
            yield objects[0], files


logger = logging.getLogger('Store')

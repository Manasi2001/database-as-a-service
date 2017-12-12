# -*- coding:utf-8 -*-
from util.html import render_progress_bar
from decimal import Decimal, getcontext, ROUND_HALF_EVEN
from django import template
from django.utils.safestring import mark_safe
from django.utils.functional import cached_property
import logging
from logical.models import MB_FACTOR, GB_FACTOR

getcontext().rounding = ROUND_HALF_EVEN
TWO = Decimal(10) ** -2
register = template.Library()

LOG = logging.getLogger(__name__)


@register.simple_tag
def render_capacity_html(database):
    try:
        message = "%d of %s (MB)" % (
            database.used_size_in_mb, (database.total_size_in_mb) or 'unlimited')
        return render_progress_bar(database.capacity * 100, message=message)
    except:
        # any error show Unkown message and log error. This avoid break page if there is a problem
        # with some database
        LOG.exception('Error getting capacity of database %s', database)
        return "Unknown"


@register.tag
def render_detailed_capacity_html(parser, token):
    '''
    Usage: {% render_detailed_progress_bar database_obj type %}
    params:
        - database_obj: Logical Database object
        - type: type of bar. can be: disk or memory

    Ex. For physical disk bar:
        {% render_detailed_progress_bar database disk %}
    Ex. For physical momery bar:
        {% render_detailed_progress_bar database memory %}
    '''

    try:
        split_contents = token.split_contents()
        obj_name = split_contents[1]
        bar_type = split_contents[2]
    except IndexError:
        raise template.TemplateSyntaxError(
            "%r tag requires 1 argument." % split_contents
        )

    return DetailedProgressBarNode(obj_name, bar_type)


class DetailedProgressBarNode(template.Node):
    COLORS = {
        'blue': '#409fdf',
        'grey': '#949398',
        'green': '#70bf45',
    }

    def __init__(self, obj_name, bar_type):
        self.obj_name = obj_name
        self.bar_type = bar_type
        self.bars = []

    def _init_vars(self, database):
        self.html = ''
        self.disk_parts = []
        self.memory_parts = []
        self.database = database
        self.databaseinfra = database.databaseinfra
        self.instance = self.databaseinfra.instances.first()
        self.is_in_memory = database.engine.engine_type.is_in_memory
        self.is_persisted = database.plan.has_persistence

    @staticmethod
    def normalize_number(n):
        n = Decimal(round(n, 2))
        return n.quantize(TWO)

    def render(self, context):
        obj = context.get(self.obj_name)
        if obj is None:
            return ''
        self._init_vars(obj)

        if self.bar_type == 'disk':
            is_in_memory_and_not_persisted = (self.is_in_memory and
                                              not self.is_persisted)
            not_in_memory_and_used_disk_none = (not self.used_disk_in_gb and
                                                not self.is_in_memory)

            if is_in_memory_and_not_persisted or not_in_memory_and_used_disk_none:
                return ''
            html = self.render_disk_bar()
        else:
            html = self.render_memory_bar()

        return mark_safe(html)

    def _make_disk_part(self, **kw):
        self.disk_parts.append(kw)

    def _make_memory_part(self, **kw):
        self.memory_parts.append(kw)

    @property
    def total_disk_in_gb(self):
        return self.normalize_number(self.instance.total_size_in_bytes * GB_FACTOR)
        host_attr = self.instance.hostname.nfsaas_host_attributes.filter(is_active=True).first()
        total_disk_in_gb = (host_attr.nfsaas_size_kb or 0.0) * MB_FACTOR
        return self.normalize_number(total_disk_in_gb)

    @property
    def used_disk_in_gb(self):
        host_attr = self.instance.hostname.nfsaas_host_attributes.filter(is_active=True).first()
        total_disk_in_gb = (host_attr.nfsaas_used_size_kb or 0.0) * MB_FACTOR
        return self.normalize_number(total_disk_in_gb)
        used_disk_in_gb = self.databaseinfra.disk_used_size_in_gb
        return self.normalize_number(used_disk_in_gb) if used_disk_in_gb is not None else None

    @property
    def used_database_in_gb(self):
        return self.normalize_number(self.instance.used_size_in_bytes * GB_FACTOR)
        if self.is_in_memory and not self.is_persisted:
            return self.used_disk_in_gb or 0
        return self.normalize_number(self.database.used_size_in_gb)

    @property
    def used_other_in_gb(self):
        if self.used_disk_in_gb is None:
            return None
        else:
            used_other_in_gb = self.used_disk_in_gb - self.used_database_in_gb
            return self.normalize_number(used_other_in_gb)

    @property
    def free_disk_in_gb(self):
        if self.used_disk_in_gb is None:
            free_disk_in_gb = self.total_disk_in_gb - self.used_database_in_gb
        else:
            free_disk_in_gb = self.total_disk_in_gb - self.used_disk_in_gb

        return self.normalize_number(free_disk_in_gb)

    @property
    def database_percent(self):
        if self.total_disk_in_gb:
            database_percent = (self.used_database_in_gb * 100) / self.total_disk_in_gb
        else:
            database_percent = 0.0

        return self.normalize_number(database_percent)

    @property
    def other_percent(self):
        if self.used_disk_in_gb is None or self.is_in_memory:
            other_percent = 0
        else:
            other_percent = (self.used_other_in_gb * 100) / self.total_disk_in_gb

        return self.normalize_number(other_percent)

    @property
    def free_percent(self):
        free_percent = 100 - (self.database_percent + self.other_percent)

        return self.normalize_number(free_percent)

    def render_disk_bar(self):
        # Fazer o loop para reiderizar com a instancia
        # self.instance = self.database.driver.get_master_instance()
        self._make_disk_part(**{
            'name': 'database',
            'label': 'Used' if self.is_in_memory else 'Database',
            'percentage': self.database_percent,
            'used_space': self.used_database_in_gb,
            'color': self.COLORS['blue']
        })
        if self.used_other_in_gb is not None and not self.is_in_memory:
            self._make_disk_part(**{
                'name': 'other',
                'label': 'Others',
                'percentage': self.other_percent,
                'used_space': self.used_other_in_gb,
                'color': self.COLORS['grey']
            })
        self._make_disk_part(**{
            'name': 'free',
            'label': 'Free',
            'percentage': self.free_percent,
            'used_space': self.free_disk_in_gb,
            'color': self.COLORS['green']
        })
        return self.render_template(self.disk_parts, 'disk')

    @property
    def total_db_memory_in_gb(self):
        return self.normalize_number(self.instance.total_size_in_bytes * GB_FACTOR)
        return self.normalize_number(self.database.total_size_in_gb)

    @property
    def used_db_memory_in_gb(self):
        return self.normalize_number(self.instance.used_size_in_bytes * GB_FACTOR)
        return self.normalize_number(self.database.used_size_in_gb)

    @property
    def used_db_memory_percent(self):
        used_memory_percent = (self.used_db_memory_in_gb * 100) / self.total_db_memory_in_gb

        return self.normalize_number(used_memory_percent)

    @property
    def free_db_memory_percent(self):
        return self.normalize_number(100 - self.used_db_memory_percent)

    @property
    def free_db_memory_size_in_gb(self):
        return self.total_db_memory_in_gb - self.used_db_memory_in_gb

    def render_memory_bar(self):
        self._make_memory_part(**{
            'name': 'database',
            'label': 'Used',
            'percentage': self.used_db_memory_percent,
            'used_space': self.used_db_memory_in_gb,
            'color': self.COLORS['blue']
        })
        self._make_memory_part(**{
            'name': 'free',
            'label': 'Free',
            'percentage': self.free_db_memory_percent,
            'used_space': self.free_db_memory_size_in_gb,
            'color': self.COLORS['green']
        })

        return self.render_template(self.memory_parts, 'memory')

    def render_template(self, parts, bar_type):
        t = template.loader.get_template('progress_bar/progress_bar.html')
        c = template.Context({
            'bars': parts,
            'bar_type': bar_type
        })
        return t.render(c)

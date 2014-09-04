# -*- coding: utf-8 -*-
from Products.Archetypes.ExtensibleMetadata import ExtensibleMetadata
from Products.CMFCore.interfaces import IPropertiesTool
from Products.CMFCore.utils import getToolByName
from Products.CMFDefault.DublinCore import DefaultDublinCoreImpl
from Products.Five.browser import BrowserView
from Products.Five.browser.pagetemplatefile import ViewPageTemplateFile
from datetime import datetime
from datetime import timedelta
from plone.app.contenttypes.migration import migration
from plone.app.contenttypes.migration.utils import ATCT_LIST
from plone.app.contenttypes.migration.utils import HAS_MULTILINGUAL
from plone.app.contenttypes.migration.utils import isSchemaExtended
from plone.browserlayer.interfaces import ILocalBrowserLayerType
from plone.dexterity.content import DexterityContent
from plone.dexterity.interfaces import IDexterityContent
from plone.z3cform.layout import wrap_form
from pprint import pformat
from z3c.form import button
from z3c.form import field
from z3c.form import form
from z3c.form.browser.checkbox import CheckBoxFieldWidget
from z3c.form.interfaces import HIDDEN_MODE
from zope import schema
from zope.component import getMultiAdapter
from zope.component import queryUtility
from zope.interface import Interface

import logging
logger = logging.getLogger(__name__)

# Schema Extender allowed interfaces

from plone.app.contenttypes.content import (
    Document,
    File,
    Folder,
    Image,
    Link,
    NewsItem,
)

PATCH_NOTIFY = [
    DexterityContent,
    DefaultDublinCoreImpl,
    ExtensibleMetadata
]

# Average time to migrate one archetype object, in milliseconds.
# This very much depends on the size of the object and system-speed
ONE_OBJECT_MIGRATION_TIME = 500


class FixBaseClasses(BrowserView):

    def __call__(self):
        """Make sure all content objects use the proper base classes.
        Instances before version 1.0b1 had no base-class.
        To update them call @@fix_base_classes on your site-root.
        """
        out = ""
        portal_types = [
            ('Document', Document),
            ('File', File),
            ('Folder', Folder),
            ('Image', Image),
            ('Link', Link),
            ('News Item', NewsItem),
        ]
        catalog = getToolByName(self.context, "portal_catalog")
        query = {}
        if HAS_MULTILINGUAL and 'Language' in catalog.indexes():
            query['Language'] = 'all'
        for portal_type, portal_type_class in portal_types:
            query['portal_type'] = portal_type
            results = catalog(query)
            for brain in results:
                obj = brain.getObject()
                if IDexterityContent.providedBy(obj):
                    object_class_name = obj.__class__.__name__
                    target_class_name = portal_type_class.__name__
                    if not object_class_name == target_class_name:
                        obj.__class__ = portal_type_class
                        out += "Make %s use %s\n as base class." % (
                            obj.Title(),
                            portal_type_class.__name__,
                        )
        return out


class MigrateFromATContentTypes(BrowserView):
    """ Migrate the default-types (except event and topic).
    This view can be called directly and it will migrate all content
    provided they were not schema-extended.
    This is also called by the migration-form below with some variables.
    """

    def __call__(self,
                 migrate=False,
                 content_types="all",
                 migrate_schemaextended_content=False,
                 migrate_references=True,
                 from_form=False):

        portal = self.context
        if not from_form and migrate not in ['1', 'True', 'true', 1]:
            url1 = '{0}/@@migrate_from_atct?migrate=1'.format(
                portal.absolute_url())
            url2 = '{0}/@@atct_migrator'.format(portal.absolute_url())
            msg = u'Warning \n'
            msg += u'-------\n'
            msg += u'You are accessing "@@migrate_from_atct" directly. '
            msg += u'This will migrate all content to dexterity!\n\n'
            msg += u'Really migrate all content now: {0}\n\n'.format(url1)
            msg += u'First select what to migrate: {0}'.format(url2)
            return msg

        helpers = getMultiAdapter((portal, self.request),
                                  name="atct_migrator_helpers")
        if helpers.linguaplone_installed():
            msg = 'Warning\n'
            msg += 'Migration aborted since Products.LinguaPlone is '
            msg += 'installed. See '
            msg += 'http://github.com/plone/plone.app.contenttypes#migration '
            msg += 'for more information.'
            return msg

        stats_before = self.stats()
        starttime = datetime.now()
        catalog = portal.portal_catalog

        # switch linkintegrity temp off
        ptool = queryUtility(IPropertiesTool)
        site_props = getattr(ptool, 'site_properties', None)
        link_integrity = site_props.getProperty('enable_link_integrity_checks',
                                                False)
        site_props.manage_changeProperties(enable_link_integrity_checks=False)

        # switch of setModificationDate on changes
        self.patchNotifyModified()

        not_migrated = []
        migrated_types = {}

        for (k, v) in ATCT_LIST.items():
            if content_types != "all" and k not in content_types:
                not_migrated.append(k)
                continue
            # test if the ct is extended beyond blobimage and blobfile
            if len(isSchemaExtended(v['iface'])) > len(v['extended_fields']) \
                    and not migrate_schemaextended_content:
                not_migrated.append(k)
                continue
            query = {
                'object_provides': v['iface'].__identifier__,
                'meta_type': v['old_meta_type'],
            }
            if HAS_MULTILINGUAL and 'Language' in catalog.indexes():
                query['Language'] = 'all'
            amount_to_be_migrated = len(catalog(query))
            starttime_for_current = datetime.now()
            logger.info("Start migrating %s objects from %s to %s" % (
                amount_to_be_migrated,
                v['old_meta_type'],
                v['type_name']))

            # call the migrator
            v['migrator'](portal)

            # logging
            duration_current = datetime.now() - starttime_for_current
            duration_human = str(timedelta(seconds=duration_current.seconds))
            logger.info("Finished migrating %s objects from %s to %s in %s" % (
                amount_to_be_migrated,
                v['old_meta_type'],
                v['type_name'],
                duration_human))
            # some data for the results-page
            migrated_types[k] = {}
            migrated_types[k]['amount_migrated'] = amount_to_be_migrated
            migrated_types[k]['old_meta_type'] = v['old_meta_type']
            migrated_types[k]['type_name'] = v['type_name']

        # if there are blobnewsitems we just migrate them silently.
        migration.migrate_blobnewsitems(portal)

        if migrate_references:
            migration.restoreReferences(portal)

        # switch linkintegrity back to what it was before migrating
        site_props.manage_changeProperties(
            enable_link_integrity_checks=link_integrity
        )

        # switch on setModificationDate on changes
        self.resetNotifyModified()

        duration = str(timedelta(seconds=(datetime.now() - starttime).seconds))
        if not_migrated:
            msg = ("The following types were not migrated: \n %s"
                   % "\n".join(not_migrated))
        else:
            msg = "Migration successful\n\n"
        msg += '\n-----------------------------\n'
        msg += 'Migration finished in: %s' % duration
        msg += '\n-----------------------------\n'
        msg += 'Migration statictics:\n'
        msg += pformat(migrated_types)
        msg += '\n-----------------------------\n'
        msg += 'State before:\n'
        msg += pformat(stats_before)
        msg += '\n-----------------------------\n'
        msg += 'Stats after:\n'
        msg += pformat(self.stats())
        msg += '\n-----------------------------\n'
        if not from_form:
            logger.info(msg)
            return msg
        else:
            stats = {
                'duration': duration,
                'before': stats_before,
                'after': self.stats(),
                'content_types': content_types,
                'migrated_types': migrated_types,
            }
            logger.info(msg)
            return stats

    def stats(self):
        results = {}
        query = {}
        catalog = self.context.portal_catalog
        if HAS_MULTILINGUAL and 'Language' in catalog.indexes():
            query['Language'] ='all'
        for brain in catalog(query):
            classname = brain.getObject().__class__.__name__
            results[classname] = results.get(classname, 0) + 1
        return results

    def patchNotifyModified(self):
        """Patch notifyModified to prevent setModificationDate() on changes

        notifyModified lives in several places and is also used on folders
        when their content changes.
        So when we migrate Documents before Folders the folders
        ModifiedDate gets changed.
        """
        patch = lambda *args: None
        for klass in PATCH_NOTIFY:
            old_notifyModified = getattr(klass, 'notifyModified', None)
            klass.notifyModified = patch
            klass.old_notifyModified = old_notifyModified

    def resetNotifyModified(self):
        """reset notifyModified to old state"""

        for klass in PATCH_NOTIFY:
            if klass.old_notifyModified is None:
                del klass.notifyModified
            else:
                klass.notifyModified = klass.old_notifyModified
            del klass.old_notifyModified


class IATCTMigratorForm(Interface):

    content_types = schema.List(
        title=u"Existing content that can be migrated",
        description=u"Select which content types you want to migrate",
        value_type=schema.Choice(
            vocabulary="plone.app.contenttypes.migration.atctypes",
        ),
        required=False,
    )

    migrate_references = schema.Bool(
        title=u"Migrate references?",
        description=(
            u"Select this option to migrate all "
            u"references to each content type. "
            u"This will rebuild the whole catalog and "
            u"might duplicate the migration-time."
        ),
        default=True
    )

    extended_content = schema.List(
        title=(
            u"Migrate content that was extended "
            u"using archetypes.schemaextender?"
        ),
        description=(
            u"Warning: You will loose all data in the extended fields!"
        ),
        value_type=schema.Choice(
            vocabulary="plone.app.contenttypes.migration.extendedtypes",
        ),
        required=False,
    )


class ATCTMigratorForm(form.Form):

    fields = field.Fields(IATCTMigratorForm)
    fields['content_types'].widgetFactory = CheckBoxFieldWidget
    fields['extended_content'].widgetFactory = CheckBoxFieldWidget
    ignoreContext = True

    @button.buttonAndHandler(u'Migrate', name='migrate')
    def handle_migrate(self, action):
        data, errors = self.extractData()
        context = self.context

        if errors:
            return

        content_types = data['content_types'] or []
        content_types.extend(data['extended_content'] or [])

        migration_view = getMultiAdapter(
            (context, self.request),
            name=u'migrate_from_atct'
        )
        # call the migration-view above to actually migrate stuff.
        results = migration_view(
            content_types=content_types,
            migrate_schemaextended_content=True,
            migrate_references=data['migrate_references'],
            from_form=True,
        )
        sdm = getToolByName(context, "session_data_manager")
        session = sdm.getSessionData(create=True)
        session.set("atct_migrator_results", results)
        url = context.absolute_url()
        self.request.response.redirect(url + "/@@atct_migrator_results")

    def updateActions(self):
        super(ATCTMigratorForm, self).updateActions()
        self.actions['migrate'].addClass("btn-danger")

    def updateWidgets(self):
        """ Overload this to hide empty widget """
        form.Form.updateWidgets(self)
        for title, widget in self.widgets.items():
            if title not in ('content_types', 'extended_content'):
                continue
            if not len(widget.items):
                # the vocabulary is empty, we hide the widget
                widget.mode = HIDDEN_MODE


ATCTMigrator = wrap_form(
    ATCTMigratorForm,
    index=ViewPageTemplateFile('atct_migrator.pt')
)


class ATCTMigratorHelpers(BrowserView):

    def objects_to_be_migrated(self):
        """ Return the number of AT objects in the portal """
        catalog = getToolByName(self.context, "portal_catalog")
        query = {'meta_type': [i['old_meta_type'] for i in ATCT_LIST.values()]}
        if HAS_MULTILINGUAL and 'Language' in catalog.indexes():
            query['Language'] ='all'
        return len(catalog(query))

    def estimated_migration_time(self):
        """ Return the estimated migration time """
        total_time = self.objects_to_be_migrated() * ONE_OBJECT_MIGRATION_TIME
        hours, remainder = divmod(total_time / 1000, 3600)
        minutes, seconds = divmod(remainder, 60)
        return {
            'hours': hours,
            'minutes': minutes,
            'seconds': seconds
        }

    def linguaplone_installed(self):
        """ Is Products.LinguaPlone installed ? """
        existing = queryUtility(ILocalBrowserLayerType, name='LinguaPlone')
        return bool(existing)


class ATCTMigratorResults(BrowserView):

    index = ViewPageTemplateFile('atct_migrator_results.pt')

    def results(self):
        sdm = self.context.session_data_manager
        session = sdm.getSessionData(create=True)
        results = session.get("atct_migrator_results", None)
        if not results:
            return False
        # results['atct_list'] = ATCT_LIST
        return results

# -*- coding: utf-8 -*-
from util import build_context_script, exec_remote_command_host, \
    get_credentials_for
from dbaas_cloudstack.models import HostAttr, PlanAttr
from dbaas_cloudstack.models import CloudStackPack
from dbaas_credentials.models import CredentialType
from dbaas_nfsaas.models import HostAttr as HostAttrNfsaas
from base import BaseInstanceStep, BaseInstanceStepMigration
from physical.configurations import configuration_factory
import logging

LOG = logging.getLogger(__name__)


class PlanStep(BaseInstanceStep):

    def __init__(self, instance):
        super(PlanStep, self).__init__(instance)
        self._pack = None

    @property
    def pack(self):
        if not self._pack:
            offering = self.infra.plan.cloudstack_attr.get_stronger_offering()
            self._pack = CloudStackPack.objects.get(
                offering__serviceofferingid=offering.serviceofferingid,
                offering__region__environment=self.environment,
                engine_type__name=self.infra.engine_name
            )
        return self._pack

    @property
    def cs_plan(self):
        return PlanAttr.objects.get(plan=self.plan)

    @property
    def host_cs(self):
        return HostAttr.objects.get(host=self.host)

    @property
    def host_nfs(self):
        try:
            return HostAttrNfsaas.objects.get(host=self.host, is_active=True)
        except HostAttrNfsaas.DoesNotExist:
            return None

    @property
    def script_variables(self):
        variables = {
            'DATABASENAME': self.database.name,
            'DBPASSWORD': self.infra.password,
            'HOST': self.host.hostname.split('.')[0],
            'ENGINE': self.plan.engine.engine_type.name,
            'MOVE_DATA': bool(self.upgrade) or bool(self.reinstall_vm),
            'DRIVER_NAME': self.infra.get_driver().topology_name(),
            'DISK_SIZE_IN_GB': self.disk_offering.size_gb(),
            'ENVIRONMENT': self.environment,
            'HAS_PERSISTENCE': self.infra.plan.has_persistence,
            'IS_READ_ONLY': self.instance.read_only,
        }

        variables['configuration'] = self.get_configuration()
        variables['GRAYLOG_ENDPOINT'] = self.get_graylog_config()
        if self.host_nfs:
            variables['EXPORTPATH'] = self.host_nfs.nfsaas_path

        variables.update(self.get_variables_specifics())
        return variables

    def get_graylog_config(self):
        credential = get_credentials_for(
            environment=self.environment,
            credential_type=CredentialType.GRAYLOG
        )
        return credential.get_parameter_by_name('endpoint_log')

    @property
    def offering(self):
        if self.resize:
            return self.resize.target_offer.offering

        return self.pack.offering

    def get_configuration(self):
        try:
            configuration = configuration_factory(
                self.infra, self.offering.memory_size_mb
            )
        except NotImplementedError:
            return None
        else:
            return configuration

    def get_variables_specifics(self):
        return {}

    def do(self):
        raise NotImplementedError

    def undo(self):
        pass

    def run_script(self, plan_script):
        script = build_context_script(self.script_variables, plan_script)

        output = {}
        return_code = exec_remote_command_host(self.host, script, output)
        if return_code != 0:
            raise EnvironmentError(
                'Could not execute script {}: {}'.format(
                    return_code, output
                )
            )

        return output


class PlanStepNewInfra(PlanStep):

    @property
    def database(self):
        from logical.models import Database
        database = Database()
        database.name = self.infra.databases_create.last().name
        return database


class PlanStepNewInfraSentinel(PlanStepNewInfra):

    @property
    def is_valid(self):
        return self.instance.is_sentinel

    def get_variables_specifics(self):
        driver = self.infra.get_driver()
        base = super(PlanStepNewInfraSentinel, self).get_variables_specifics()
        base.update(driver.master_parameters(
            self.instance, self.infra.instances.first()
        ))
        return base


class PlanStepRestore(PlanStep):

    @property
    def host_nfs(self):
        try:
            return HostAttrNfsaas.objects.filter(
                host=self.host, is_active=False
            ).last()
        except HostAttrNfsaas.DoesNotExist:
            return None

    def get_variables_specifics(self):
        driver = self.infra.get_driver()
        base = super(PlanStepRestore, self).get_variables_specifics()
        if self.restore.is_master(self.instance) or self.restore.is_slave(self.instance):
            base.update(driver.master_parameters(
                self.instance, self.restore.master_for(self.instance)
            ))
        return base


class PlanStepUpgrade(PlanStep):

    @property
    def plan(self):
        plan = super(PlanStepUpgrade, self).plan
        return plan.engine_equivalent_plan


class Initialization(PlanStep):

    def __unicode__(self):
        return "Executing plan initial script..."

    def get_variables_specifics(self):
        driver = self.infra.get_driver()
        return driver.initialization_parameters(self.instance)

    def do(self):
        if self.is_valid:
            self.run_script(self.plan.script.initialization_template)


class Configure(PlanStep):

    def __unicode__(self):
        return "Executing plan configure script..."

    def get_variables_specifics(self):
        driver = self.infra.get_driver()
        return driver.configuration_parameters(self.instance)

    def do(self):
        if self.is_valid:
            self.run_script(self.plan.script.configuration_template)


class StartReplication(PlanStep):

    def __unicode__(self):
        return "Executing replication start script..."

    def get_variables_specifics(self):
        driver = self.infra.get_driver()
        return driver.start_replication_parameters(self.instance)

    def do(self):
        if self.is_valid:
            self.run_script(self.plan.script.start_replication_template)


class StartReplicationFirstNode(StartReplication):

    @property
    def is_valid(self):
        base = super(StartReplication, self).is_valid
        if not base:
            return base

        return self.instance == self.infra.instances.first()


class InitializationForUpgrade(Initialization, PlanStepUpgrade):
    pass


class ConfigureForUpgrade(Configure, PlanStepUpgrade):
    pass


class InitializationForNewInfra(Initialization, PlanStepNewInfra):
    pass


class ConfigureForNewInfra(Configure, PlanStepNewInfra):
    pass


class StartReplicationNewInfra(StartReplication, PlanStepNewInfra):
    pass


class StartReplicationFirstNodeNewInfra(
    StartReplicationFirstNode, PlanStepNewInfra
):
    pass


class InitializationForNewInfraSentinel(
    PlanStepNewInfraSentinel, Initialization
):
    pass


class ConfigureForNewInfraSentinel(PlanStepNewInfraSentinel, Configure):
    pass


class ConfigureForResizeLog(Configure):

    def get_variables_specifics(self):
        driver = self.infra.get_driver()
        base = driver.configuration_parameters_for_log_resize(self.instance)
        base.update({'CONFIGFILE_ONLY': True})
        return base


class InitializationMigration(Initialization, BaseInstanceStepMigration):

    def get_variables_specifics(self):
        driver = self.infra.get_driver()
        return driver.initialization_parameters(self.instance.future_instance)

    @property
    def offering(self):
        offering_base = self.infra.cs_dbinfra_offering.get().offering
        return offering_base.equivalent_offering


class ConfigureMigration(Configure, BaseInstanceStepMigration):

    def get_variables_specifics(self):
        driver = self.infra.get_driver()
        return driver.configuration_parameters_migration(
            self.instance.future_instance
        )

    @property
    def offering(self):
        offering_base = self.infra.cs_dbinfra_offering.get().offering
        return offering_base.equivalent_offering


class ConfigureRestore(PlanStepRestore, Configure):

    def __init__(self, instance, **kwargs):
        super(ConfigureRestore, self).__init__(instance)
        self.kwargs = kwargs

    def get_variables_specifics(self):
        base = super(ConfigureRestore, self).get_variables_specifics()

        base.update(self.kwargs)
        base['CONFIGFILE_ONLY'] = True,
        base['CREATE_SENTINEL_CONFIG'] = True

        driver = self.infra.get_driver()

        if self.restore.is_master(self.instance) or self.restore.is_slave(self.instance):
            base.update(driver.master_parameters(
                self.instance, self.restore.master_for(self.instance)
            ))

        return base

class ConfigureOnlyDBConfigFile(Configure):
    def get_variables_specifics(self):
        base = super(ConfigureOnlyDBConfigFile, self).get_variables_specifics()
        base.update({'CONFIGFILE_ONLY': True})
        return base


class ConfigureForUpgradeOnlyDBConfigFile(ConfigureOnlyDBConfigFile, PlanStepUpgrade):
    pass


class ResizeConfigure(ConfigureOnlyDBConfigFile):

    def do(self):
        self._pack = self.resize.target_offer
        super(ResizeConfigure, self).do()

    def undo(self):
        self._pack = self.resize.source_offer
        super(ResizeConfigure, self).undo()

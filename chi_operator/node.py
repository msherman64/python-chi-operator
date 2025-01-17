from argparse import FileType
import configparser
from collections import defaultdict
import re

import click
from dracclient.client import DRACClient
from ironicclient.exc import HTTPNotFound
import requests
from requests.packages.urllib3.exceptions import InsecureRequestWarning
import warnings

from .base import BaseCommand

IRONIC_CLIENT_VERSION = 1


@click.group(help="Subcommands related to bare metal nodes")
def node():
    pass


class NodeAssignSwitchIDsCommand(BaseCommand):
    @staticmethod
    @node.command(name='assign-switch-id')
    def cli():
        """Assign switch_id attributes for Ironic ports."""
        return NodeAssignSwitchIDsCommand().run()

    @staticmethod
    def uc_assignment_strategy(cls, node, port):
        # Names are like c01, nc24.
        # Strip prefix and cast as integer.
        if node.name.startswith('nc'):
            link = port.local_link_connection
            switch_info = link["switch_info"]
            if switch_info == "chameleon-corsa1":
                start_id = 100
            elif switch_info == "chameleon-corsa2":
                start_id = 132
            else:
                raise ValueError((
                    "Cannot figure out new port ID for node {}: unknown switch {}"
                    .format(node.uuid, switch_info)
                ))
            return start_id + int(re.sub(r'^P ', '', link["port_id"]))
        elif node.name.startswith('c'):
            return 200 + int(re.sub(r'^[a-z]+', '', node.name))
        else:
            raise ValueError("Cannot figure out new port ID for node {}".format(node.uuid))

    @staticmethod
    def tacc_assignment_strategy(cls, node, port):
        link = port.local_link_connection
        switch_info = link["switch_info"]

        if switch_info == "roc-ax35-sw1":
            start_id = 100
            return start_id + int(re.sub(r'^P ', '', link["port_id"]))
        else:
            raise ValueError((
                "Cannot figure out new port ID for node {}: unknown switch {}"
                .format(node.uuid, switch_info)
            ))

    REGION_STRATEGIES = {
        "CHI@TACC": tacc_assignment_strategy,
        "CHI@UC": uc_assignment_strategy,
    }

    def run(self):
        region_name = self.session.region_name
        ironic = self.ironic()

        ports_for_update = []

        for node in ironic.node.list(sort_key='name'):
            ports = ironic.port.list(detail=True, node=node.uuid)

            if not ports:
                self.log.error("No ports found for node {}".format(node.uuid))
                continue

            port = ports[0]
            switch_info = port.local_link_connection["switch_info"]
            port_id = port.local_link_connection["port_id"]

            assignment_strategy = self.REGION_STRATEGIES.get(region_name)

            if not assignment_strategy:
                self.log.error("No port assignment strategy found!")
                continue

            try:
                switch_id = assignment_strategy(node, port)
            except:
                self.log.exception("Failed to assign switch_id for node")
                continue

            if int(port.local_link_connection["switch_id"].replace(":", "")) != switch_id:
                ports_for_update.append(dict(
                    node=node, uuid=port.uuid,
                    switch_id=switch_id, switch_info=switch_info, port_id=port_id
                ))

            # Ensure other secondary ports have switch_id unset
            for p in ports[1:]:
                link = p.local_link_connection
                if int(link["switch_id"].replace(":", "")) != 0:
                    ports_for_update.append(dict(
                        node=node, uuid=p.uuid,
                        switch_id=0, switch_info=link["switch_info"], port_id=link["port_id"]
                    ))

        for p in sorted(ports_for_update, key=lambda p: p["node"].name):
            node = p["node"]
            node_uuid = node.uuid
            node_name = node.name
            port_uuid = p["uuid"]
            padded_switch_id = str(p["switch_id"]).zfill(16)

            patch = [
                dict(
                    path="/local_link_connection/switch_id",
                    value=padded_switch_id,
                    op="replace"
                )
            ]

            try:
                force_maintenance = not node.maintenance

                if force_maintenance:
                    ironic.node.set_maintenance(node_uuid, True,
                        maint_reason="node-assign-switch-ids: updating local_link_connection")

                ironic.port.update(port_uuid, patch)

                if force_maintenance:
                    ironic.node.set_maintenance(node_uuid, False)

                self.log.info("{} port {} [{}:{}] updated to switch_id={}".format(
                    node_name, port_uuid, p["switch_info"], p["port_id"], padded_switch_id))
            except:
                self.log.exception("failed to update port {}".format(p["uuid"]))


class NodeRotateIPMIPasswordCommand(BaseCommand):

    FACTORY_PASSWORD = "calvin"

    def register_args(self, parser):
        parser.add_argument("nodes", metavar="NODE", nargs="+")
        parser.add_argument("--password-file", type=FileType("r"),
                            required=True)
        parser.add_argument("--old-password-file", type=FileType("r"))

    def run(self):
        new_password = self.args.password_file.read().strip()
        self.args.password_file.close()

        if self.args.old_password_file:
            old_password = self.args.old_password_file.read().strip()
            self.args.old_password_file.close()
        else:
            # TODO: maybe depends if we can detect BMC vendor
            old_password = self.FACTORY_PASSWORD

        ironic = self.ironic()
        nodes = ironic.node.list(detail=True)

        def find_node(name_or_id):
            matching = [
                n for n in nodes
                if n.uuid == name_or_id or n.name == name_or_id
            ]
            if matching:
                return matching[0]
            else:
                raise ValueError("No node matched '{}'".format(name_or_id))

        for n in self.args.nodes:
            node = find_node(n)
            node_id = node.uuid

            if node.provision_state not in ["active", "available"]:
                raise ValueError(
                    "Node {} in invalid provision state".format(node_id))

            self.log.info("Processing {} ({}):".format(node_id, node.name))

            if not node.maintenance:
                ironic.node.set_maintenance(
                    node_id, True, maint_reason="Updating IPMI password")
                self.log.info("  Put node into maintenance mode")

            try:
                driver_info = node.driver_info
                drac = DRACClient(
                    host=driver_info.get("ipmi_address"),
                    username=driver_info.get("ipmi_username"),
                    password=old_password)

                # Temporarily disable insecure HTTPS warnings while we call out
                # to the iDRAC Redfish address over HTTPS
                requests.packages.urllib3.disable_warnings(
                    InsecureRequestWarning)
                drac.set_idrac_settings({"Users.2#Password": new_password})
                warnings.resetwarnings()

                self.log.info("  Updated iDRAC password")

                ironic.node.update(node_id, patch=[
                    dict(
                        path="/driver_info/ipmi_password",
                        value=new_password,
                        op="replace"
                    )
                ])

                self.log.info("  Updated Ironic node ipmi_password")

                # Test that the connection works
                boot_dev = ironic.node.get_boot_device(node_id)
                assert "boot_device" in boot_dev
            except:
                self.log.exception("  Failed to update password")
                break
            finally:
                # Restore original maintenance state
                if not node.maintenance:
                    ironic.node.set_maintenance(node_id, False)
                    self.log.info("  Reverted node maintenance state")


class NodeEnrollCommand(BaseCommand):
    DEFAULT_PROPERTIES = {
        'capabilities': 'boot_option:local',
    }

    ALLOWED_SUBSECTIONS = ['ports']

    @staticmethod
    @node.command(name='enroll')
    @click.option('--node-conf', 'node_conf', type=click.File('r'))
    @click.argument('nodes', nargs=-1)
    def cli(node_conf, nodes):
        """
        Enroll nodes in Ironic/Blazar from a configuration file. The configuration
        file declares nodes by name and includes important configuration, namely the
        IPMI authentication details and the physical switch information and NIC MAC
        addresses for the bare metal node.

        Reads a node configuration from the first positional argument, and defaults
        to looking for a file "nodes.conf".

        Sample configuration format::

            \b
            [node01]
            # Give a name to the node class; Chameleon uses node class prefixes
            # followed by specifiers, e.g. compute_haswell or gpu_rtx
            node_type = compute_haswell
            ipmi_username = root
            ipmi_password = hopefully_not_default
            ipmi_address = 10.10.10.1
            # Optional, defaults to this value.
            ipmi_port = 623
            # Arbitrary terminal port; this is used to plumb a socat process to allow
            # reading and writing to a virtual console. It is just important that it does
            # not conflict with another node or host process.
            ipmi_terminal_port = 30133
            # Each NIC that should be manageable has its own section. The name of
            # the section does not matter so long as the first part is the name of a
            # node defined elsewhere in the config. This example uses the consistent
            # network device name of the interface.
            [node01.ports.eno1]
            switch_name = LeafSwitch01
            switch_port_id = Te 1/10/1
            mac_address = 00:00:de:ad:be:ef
            [node01.ports.eno2]
            switch_name = LeafSwitch01-01
            switch_port_id = Te 1/4/1
            mac_address = 00:00:de:ad:be:f0

        """
        return NodeEnrollCommand().run(node_conf=node_conf, nodes=nodes)

    def run(self, node_conf=None, nodes=None):
        config = configparser.ConfigParser()
        if node_conf:
            config.read_file(node_conf)

        node_configs = defaultdict(lambda: dict(
            driver='ipmi',
            driver_info={},
            network_interface='neutron',
            resource_class='baremetal',
            properties=self.DEFAULT_PROPERTIES.copy(),
            ports=[]
        ))
        for section in config.sections():
            if '.' not in section:
                # Top-level node options
                node_configs[section]['name'] = section
                for key, value in config[section].items():
                    bucket = 'driver_info' if key.startswith('ipmi') else 'properties'
                    node_configs[section][bucket][key] = value
            else:
                node, subsection, sub_id = section.split('.')
                if subsection not in self.ALLOWED_SUBSECTIONS:
                    raise ValueError(
                        f'Unknown subsection {subsection} for {node}! '
                        f'Allowed values: {",".join(self.ALLOWED_SUBSECTIONS)}')
                sub = dict(config[section].items())
                sub['id'] = sub_id
                node_configs[node][subsection].append(sub)

        # Allow user to filter list
        if nodes:
            node_configs = {
                node: conf for node, conf in node_configs
                if node in nodes
            }

        # Pre-fetch the list of existing Blazar hosts; there is no great way
        # to look up by any reasonable ID or name.
        blazar_hosts = self.blazar().host.list()
        for node, conf in node_configs.items():
            try:
                self.enroll_node(conf, blazar_hosts)
            except:
                self.log.exception(f'Failed to enroll node {node}!')

    def _to_ironic_patch(self, props):
        def to_patch(obj, path=''):
            patch = []
            for k, v in obj.items():
                if isinstance(v, dict):
                    patch.extend(to_patch(v, f'{path}/{k}'))
                else:
                    patch.append({
                        'op': 'add',
                        'path': f'{path}/{k}',
                        'value': v
                    })
            return patch
        return to_patch(props)

    def _ensure_ironic_node(self, ironic, node_conf):
        node_name = node_conf['name']
        node_params = node_conf.copy()
        # Ports are configured via a separate call
        node_params.pop('ports', None)

        try:
            node = ironic.node.get(node_name)
            if node.provision_state != 'manageable':
                self.log.debug(f'Setting Ironic node {node.uuid} to manageable')
                ironic.node.set_provision_state(node.uuid, 'manage')
                ironic.node.wait_for_provision_state(node.uuid, 'manageable')
            patch = self._to_ironic_patch(node_params)
            self.log.debug(f'Ironic node patch for {node.uuid}: {patch}')
            ironic.node.update(node.uuid, patch)
            self.log.info(f'Updated Ironic node {node.uuid} ({node_name})')
        except HTTPNotFound:
            node_params['name'] = node_name
            self.log.debug(f'Ironic node create: {node_params}')
            node = ironic.node.create(**node_params)
            self.log.info(f'Created Ironic node {node.uuid} ({node_name})')
        return node

    def _ensure_ironic_ports(self, ironic, ironic_node, node_port_confs):
        node_id = ironic_node.uuid
        ironic_ports = {
            p.address: p for p in ironic.port.list(node=node_id)
        }
        conf_ports = {
            p['mac_address']: p for p in node_port_confs
        }

        existing = set(ironic_ports.keys())
        configured = set(conf_ports.keys())

        # TODO: this is a bit hairy because Ironic will, if it encounters a tie
        # in a decision between which port to attach a Neutron VIF to, pick the
        # first one. This means that if we have two NICs and 1 is the data plane
        # and one is the control plane, and we have no other way of
        # differentiating them, we need to put the control plane first. The
        # solution here is to separate the control and data planes as different
        # Neutron physical networks.

        # Remove those not configured
        for mac in (existing - configured):
            port_id = ironic_ports[mac].uuid
            ironic.port.delete(port_id)
            self.log.info(f'Deleted Ironic port {port_id} for {node_id}')

        def port_params(mac):
            return {
                'address': mac,
                'local_link_connection': {
                    # We don't use switch_ids at the moment but this should
                    # be the MAC of the switch's management interface.
                    'switch_id': '00:00:00:00:00:00',
                    'port_id': conf_ports[mac]['switch_port_id'],
                    'switch_info': conf_ports[mac]['switch_name'],
                },
                'pxe_enabled': True,
                'extra': {
                    # 'id' of port config is its Linux device by convention.
                    # This extra property is just for bookkeeping/convenience.
                    'device': conf_ports[mac]['id'],
                },
            }

        # Add missing from configured
        for mac in (configured - existing):
            create_kwargs = port_params(mac)
            self.log.debug(f'Ironic port create for {node_id}: {create_kwargs}')
            port = ironic.port.create(
                node_uuid=node_id,
                **create_kwargs)
            self.log.info(f'Created Ironic port {port.uuid} for {node_id}')

        # Update existing from configured
        for mac in (existing & configured):
            port_id = ironic_ports[mac].uuid
            update_kwargs = port_params(mac)
            self.log.debug(f'Ironic port {port_id} update: {update_kwargs}')
            ironic.port.update(port_id, self._to_ironic_patch(update_kwargs))
            self.log.info(f'Updated Ironic port {port_id} for {node_id}')

    def _ensure_blazar_host(self, blazar, ironic_node, blazar_hosts):
        node_uuid = ironic_node.uuid
        host_properties = {
            'node_type': ironic_node.properties['node_type'],
            'node_name': ironic_node.name,
            'uid': node_uuid,
        }
        host = next(iter([
            h for h in blazar_hosts
            if h['hypervisor_hostname'] == node_uuid
        ]), None)
        if host:
            host = blazar.host.update(host['id'], host_properties)
            self.log.info(f'Updated Blazar host {host["id"]}')
        else:
            host = blazar.host.create(node_uuid, **host_properties)
            self.log.info(f'Created Blazar host {host["id"]}')
        return host

    def enroll_node(self, node_conf, blazar_hosts):
        ironic = self.ironic()
        blazar = self.blazar()

        node = self._ensure_ironic_node(ironic, node_conf)
        self._ensure_ironic_ports(ironic, node, node_conf['ports'])

        if node.provision_state != 'available':
            self.log.debug(f'Setting Ironic node {node.uuid} to available')
            ironic.node.set_provision_state(node.uuid, 'provide')
            ironic.node.wait_for_provision_state(node.uuid, 'available')

        ironic.node.set_console_mode(node.uuid, True)

        self._ensure_blazar_host(blazar, node, blazar_hosts)

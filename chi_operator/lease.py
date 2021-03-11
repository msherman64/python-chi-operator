from operator import itemgetter

import click
from click_spinner import spinner
from dateutil.parser import parse
from tabulate import tabulate
import yaml

from .base import BaseCommand
from .util import now


def log(msg):
    click.echo(msg, err=True)


@click.group(help="Subcommands related to reservations and leases")
def lease():
    pass


@lease.group(help="Manage resources associated with leases")
def resource():
    pass


class LeaseResourceListCommand(BaseCommand):
    @staticmethod
    @resource.command(name="list")
    @click.argument("lease_id")
    @click.option("--conflicts/--no-conflicts", default=False, help=(
        "Show future leases against the same resources at this lease"))
    def cli(lease_id, conflicts=None):
        """Retrieve a list of all resources reserved under a lease.
        """
        return LeaseResourceListCommand().run(lease_id=lease_id, show_conflicts=conflicts)

    def run(self, lease_id=None, show_conflicts=False):
        assert lease_id is not None
        log("Fetching lease")
        blazar = self.blazar()
        lease = blazar.lease.get(lease_id)

        def _with_conflicts(summary):
            if show_conflicts:
                summary["headers"].append("Conflicts")
                if summary["rows"]:
                    summary["rows"] = [
                        row + (yaml.dump(h["conflicts"], default_flow_style=False),)
                        for row, h in zip(summary["rows"], summary["raw"])
                    ]
            return summary

        host_summary = _with_conflicts(self._get_host_summary(lease))
        network_summary = _with_conflicts(self._get_network_summary(lease))
        # floating_ip_summary = _with_conflicts(self._get_floating_ip_summary(lease))
        log((
            "WARN: enumerating Floating IP allocations is not yet supported. "
            "Floating IP reservations will not be reported here."))

        click.echo(f"Host reservations for {lease['id']}:")
        click.echo(tabulate(
            host_summary["rows"],
            headers=host_summary["headers"],
            tablefmt="fancy_grid"
        ))
        click.echo(f"Network reservations for {lease['id']}:")
        click.echo(tabulate(
            network_summary["rows"],
            headers=network_summary["headers"],
            tablefmt="fancy_grid"
        ))
        # click.echo(f"Floating IP reservations for {lease['id']}:")
        # click.echo(tabulate(
        #     floating_ip_summary["rows"],
        #     headers=floating_ip_summary["headers"],
        #     tablefmt="fancy_grid"
        # ))

    def _get_host_summary(self, lease):
        def _to_row(host):
            return {"uuid": host["hypervisor_hostname"], "name": host.get("node_name")}
        # Sort by node name
        hosts_in_lease = sorted(
            self._generic_summary(lease, "host", _to_row), key=itemgetter("name"))
        host_rows = [(h["uuid"], h["name"],) for h in hosts_in_lease]
        host_headers = ["UUID", "Name"]
        return {"rows": host_rows, "headers": host_headers, "raw": hosts_in_lease}

    def _get_network_summary(self, lease):
        def _to_row(net):
            return {"segment_id": net["segment_id"]}
        nets_in_lease = sorted(
            self._generic_summary(lease, "network", _to_row), key=itemgetter("segment_id"))
        rows = [(n["segment_id"],) for n in nets_in_lease]
        headers = ["Segment ID"]
        return {"rows": rows, "headers": headers, "raw": nets_in_lease}

    def _get_floating_ip_summary(self, lease):
        def _to_row(fip):
            return {"ip_address": fip["ip_address"]}
        fips_in_lease = self._generic_summary(lease, "floatingip", _to_row)
        rows = [(f["ip_address"],) for f in fips_in_lease]
        headers = ["Floating IP"]
        return {"rows": rows, "headers": headers, "raw": fips_in_lease}

    def _generic_summary(self, lease, resource_type, transform_fn):
        blazar = self.blazar()
        blazar_resource = getattr(blazar, resource_type)
        lease_end = parse(lease["end_date"])

        log(f"Fetching all allocatable {resource_type}s")
        with spinner():
            resource_map = {r["id"]: r for r in blazar_resource.list()}
        log(f"Fetching all {resource_type} reservations")
        resources_in_lease = []
        with spinner():
            for alloc in blazar_resource.list_allocations():
                if any(res["lease_id"] == lease["id"] for res in alloc["reservations"]):
                    in_lease = transform_fn(resource_map[alloc["resource_id"]])
                    in_lease["conflicts"] = [
                        res for res in
                        alloc["reservations"]
                        if parse(res["start_date"]) > lease_end
                    ]
                    resources_in_lease.append(in_lease)
        return resources_in_lease

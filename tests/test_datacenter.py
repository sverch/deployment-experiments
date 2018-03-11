import boto3
from moto import mock_ec2

from deployment_experiments.datacenter import Datacenter, DatacenterInventory
import ipaddress


@mock_ec2
def test_datacenter():
    # Create two datacenters
    dc = Datacenter()
    dc1_id = dc.create()
    dc2_id = dc.create()
    assert dc2_id != dc1_id

    # Make sure we can discover them again
    dc1_vpcs = dc.discover(dc1_id)
    assert len(dc1_vpcs["Vpcs"]) == 1

    dc2_vpcs = dc.discover(dc2_id)
    assert len(dc2_vpcs["Vpcs"]) == 1

    # Make sure we can discover them again directly with the ec2 api
    ec2 = boto3.client("ec2")
    vpcs = ec2.describe_vpcs(VpcIds=[dc1_id])
    assert len(vpcs["Vpcs"]) == 1

    ec2 = boto3.client("ec2")
    vpcs = ec2.describe_vpcs(VpcIds=[dc2_id])
    assert len(vpcs["Vpcs"]) == 1

    # Make sure the CIDR blocks do not overlap
    def get_cidr(dc_id):
        dc = Datacenter()
        return dc.discover(dc_id)["Vpcs"][0]["CidrBlock"]
    dc1_cidr = ipaddress.ip_network(unicode(get_cidr(dc1_id)))
    dc2_cidr = ipaddress.ip_network(unicode(get_cidr(dc2_id)))
    assert not dc1_cidr.overlaps(dc2_cidr)

    # Try to get them from the DC inventory
    dc_inventory = DatacenterInventory()
    dc_ids = dc_inventory.discover()
    assert len(dc_ids) == 2
    dc = Datacenter()
    dc_cidrs = [ipaddress.ip_network(unicode(get_cidr(dc_id)))
                for dc_id in dc_ids]
    assert len(dc_cidrs) == 2
    assert not dc_cidrs[0].overlaps(dc_cidrs[1])

    # Destroy them and make sure they no longer exist
    dc.destroy(dc1_id)
    dc.destroy(dc2_id)
    dc1_vpcs = dc.discover(dc1_id)
    assert len(dc1_vpcs["Vpcs"]) == 0
    dc2_vpcs = dc.discover(dc2_id)
    assert len(dc2_vpcs["Vpcs"]) == 0
    dc_inventory = DatacenterInventory()
    dc_ids = dc_inventory.discover()
    assert len(dc_ids) == 0

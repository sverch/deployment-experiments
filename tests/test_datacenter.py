import boto3
import pytest
from moto import mock_ec2

from deployment_experiments.datacenter import Datacenter
import ipaddress


def run_datacenter_test():
    # Create two datacenters
    dc = Datacenter()
    original_count = len(dc.do_list()["Named"])
    dc1_id = dc.provision("unittest_foo")["Id"]
    assert len(dc.do_list()["Named"]) == original_count + 1
    dc1_cidr = dc.discover("unittest_foo")["CidrBlock"]
    dc2_id = dc.provision("unittest_bar", address_range_excludes=[dc1_cidr])["Id"]
    assert len(dc.do_list()["Named"]) == original_count + 2
    assert dc2_id != dc1_id

    # Make sure we can discover them again
    assert dc.discover("unittest_foo")["Id"] == dc1_id
    assert dc.discover("unittest_bar")["Id"] == dc2_id

    # Make sure we can discover them again directly with the ec2 api
    ec2 = boto3.client("ec2")
    vpcs = ec2.describe_vpcs(VpcIds=[dc1_id])
    assert len(vpcs["Vpcs"]) == 1

    ec2 = boto3.client("ec2")
    vpcs = ec2.describe_vpcs(VpcIds=[dc2_id])
    assert len(vpcs["Vpcs"]) == 1

    # Make sure the CIDR blocks do not overlap
    dc1_cidr = ipaddress.ip_network(unicode(
        dc.discover("unittest_foo")["CidrBlock"]))
    dc2_cidr = ipaddress.ip_network(unicode(
        dc.discover("unittest_bar")["CidrBlock"]))
    assert not dc1_cidr.overlaps(dc2_cidr)

    # Run noop import just to make sure it runs
    dc.do_import("unittest_foo", dc1_id)
    dc.do_import("unittest_bar", dc2_id)

    # Destroy them and make sure they no longer exist
    dc.destroy("unittest_foo")
    assert len(dc.do_list()["Named"]) == original_count + 1
    dc.destroy("unittest_bar")
    assert len(dc.do_list()["Named"]) == original_count + 0

    assert not dc.discover("unittest_foo")
    assert not dc.discover("unittest_bar")


@mock_ec2
@pytest.mark.mock
def test_datacenter_mock():
    run_datacenter_test()


@pytest.mark.real
def test_datacenter_real():
    run_datacenter_test()

import boto3
import pytest
from moto import mock_ec2

from deployment_experiments.network import Network
from deployment_experiments.datacenter import Datacenter


def run_network_test():

    # Helper function, because moto doesn't support this...
    ec2 = boto3.client("ec2")

    def get_internet_gateways_for_vpc(vpc_id):
        # XXX: Internet gateway filter dicts haven't been implemented for moto
        # yet, so I have to do this manually.
        igws = ec2.describe_internet_gateways()
        vpc_igws = {}
        vpc_igws["InternetGateways"] = []
        for igw in igws["InternetGateways"]:
            for attachment in igw["Attachments"]:
                if attachment["VpcId"] == vpc_id:
                    vpc_igws["InternetGateways"].append(igw)
        return vpc_igws

    # Provision public and private networks
    net = Network()
    public_subnets = net.provision("unittest.public")
    assert len(public_subnets) == 3
    private_subnets = net.provision("unittest.private")
    assert len(private_subnets) == 3
    net.add_path(["unittest.public", "unittest.private"])
    net.expose("unittest.public")

    # Make sure I can discover them based on service name
    assert len(net.discover("unittest.public")) == 3
    assert len(net.discover("unittest.private")) == 3

    # Get subnet Ids
    private_ids = [private_subnet["Id"] for private_subnet in private_subnets]
    public_ids = [public_subnet["Id"] for public_subnet in public_subnets]

    # Make sure they are provisioned in the same datacenter
    dc_id = None
    for subnet in ec2.describe_subnets(SubnetIds=public_ids)["Subnets"]:
        if not dc_id:
            dc_id = subnet["VpcId"]
        assert subnet["VpcId"] == dc_id
    for subnet in ec2.describe_subnets(SubnetIds=private_ids)["Subnets"]:
        assert subnet["VpcId"] == dc_id

    # Make sure I can discover them based on tags with EC2 directly
    name_filter = {'Name': "tag:cloud-deployer-network", 'Values': ["public"]}
    public_subnets = ec2.describe_subnets(Filters=[name_filter])
    assert len(public_subnets["Subnets"]) == 3

    name_filter = {'Name': "tag:cloud-deployer-network", 'Values': ["private"]}
    private_subnets = ec2.describe_subnets(Filters=[name_filter])
    assert len(private_subnets["Subnets"]) == 3

    # Make sure they show up when I list them
    subnet_info = net.do_list()
    assert len(subnet_info["unittest"]["public"]) == 3
    assert len(subnet_info["unittest"]["private"]) == 3

    # Make sure each subnet has a route table, and that the internet gateways
    # are set up correctly.
    has_gateway = False
    for subnet_id in public_ids:
        subnet_filter = {
                'Name': 'association.subnet-id',
                'Values': [subnet_id]}
        route_tables = ec2.describe_route_tables(Filters=[subnet_filter])
        assert len(route_tables["RouteTables"]) == 1
        route_table = route_tables["RouteTables"][0]
        routes = route_table["Routes"]
        for route in routes:
            if "GatewayId" in route and route["GatewayId"] != "local":
                has_gateway = True
    assert has_gateway
    has_gateway = False
    for subnet_id in private_ids:
        subnet_filter = {
                'Name': 'association.subnet-id',
                'Values': [subnet_id]}
        route_tables = ec2.describe_route_tables(Filters=[subnet_filter])
        assert len(route_tables["RouteTables"]) == 1
        route_table = route_tables["RouteTables"][0]
        routes = route_table["Routes"]
        for route in routes:
            if "GatewayId" in route and route["GatewayId"] != "local":
                has_gateway = True
    assert not has_gateway

    # Now destroy them and make sure everything gets cleaned up
    dc = Datacenter()

    net.destroy("unittest.public")
    public_subnets = net.discover("unittest.public")
    assert len(public_subnets) == 0
    assert dc.discover("unittest")

    net.destroy("unittest.private")
    private_subnets = net.discover("unittest.private")
    assert len(private_subnets) == 0
    assert not dc.discover("unittest")

    # Make sure my internet gateway are all gone
    assert len(get_internet_gateways_for_vpc(dc_id)["InternetGateways"]) == 0


@mock_ec2
@pytest.mark.mock
def test_network_mock():
    run_network_test()


@pytest.mark.real
def test_network_real():
    run_network_test()

import boto3
from moto import mock_ec2

from deployment_experiments.network import Network
from deployment_experiments.datacenter import Datacenter


@mock_ec2
def test_networks():
    # Provision public and private networks
    net = Network()
    public_ids = net.provision(network_name="public")
    private_ids = net.provision(colocated_network="public",
                                network_name="private")

    # Make sure I can discover them based on service name
    public_subnets = net.discover("public")
    assert len(public_subnets) == 3
    assert sorted(public_subnets) == sorted(public_ids)

    private_subnets = net.discover("private")
    assert len(private_subnets) == 3
    assert sorted(private_subnets) == sorted(private_ids)

    # Make sure they are provisioned in the same datacenter
    dc_id = None
    ec2 = boto3.client("ec2")
    for subnet in ec2.describe_subnets(SubnetIds=public_subnets)["Subnets"]:
        if not dc_id:
            dc_id = subnet["VpcId"]
        assert subnet["VpcId"] == dc_id
    for subnet in ec2.describe_subnets(SubnetIds=private_subnets)["Subnets"]:
        assert subnet["VpcId"] == dc_id
    net.colocated(public_subnets + private_subnets)

    # Make sure I can discover them based on tags with EC2 directly
    name_filter = {'Name': "tag:cloud-deployer-network", 'Values': ["public"]}
    public_subnets = ec2.describe_subnets(Filters=[name_filter])
    assert len(public_subnets["Subnets"]) == 3

    name_filter = {'Name': "tag:cloud-deployer-network", 'Values': ["private"]}
    private_subnets = ec2.describe_subnets(Filters=[name_filter])
    assert len(private_subnets["Subnets"]) == 3

    # Now destroy them and make sure everything gets cleaned up
    dc = Datacenter()

    net.destroy("public")
    public_subnets = net.discover("public")
    assert len(public_subnets) == 0
    assert len(dc.discover(dc_id)["Vpcs"]) == 1

    net.destroy("private")
    private_subnets = net.discover("private")
    assert len(private_subnets) == 0
    assert len(dc.discover(dc_id)["Vpcs"]) == 0

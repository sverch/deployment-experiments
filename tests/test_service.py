import boto3
import time
import pytest
from moto import mock_ec2, mock_autoscaling, mock_elb, mock_route53
from deployment_experiments.service import LoadBalancer, Service, ServiceDns
from deployment_experiments.service import NatGatewayService
from deployment_experiments.datacenter import Datacenter
from deployment_experiments.virtual_machine import VirtualMachine
import ipaddress


@mock_ec2
def test_nat_gateway():
    nat = NatGatewayService()
    nat.provision("unittest.web-nat")
    nat.destroy("unittest.web-nat")


def run_service_test():
    user_data = """#cloud-config
repo_update: true
repo_upgrade: all

packages:
  - nginx

runcmd:
 - service nginx start"""
    image = VirtualMachine(user_data=user_data, plugins=[])

    # Create the provisioner objects
    lb = LoadBalancer()
    web = Service()
    dns = ServiceDns()

    # Provision all the resources
    lb.provision("unittest.web-lb")
    web.provision("unittest.web", lb.discover("unittest.web-lb")["Id"], image)
    dns.provision("foo.myexamplesite.com",
                  lb.discover("unittest.web-lb")["DNSName"])

    # Deal with networking
    lb.expose("unittest.web-lb")
    web.allow("unittest.web", "unittest.web-lb")

    # Networking
    ec2 = boto3.client("ec2")
    dc = Datacenter()
    dc_id = dc.discover("unittest")["Id"]
    subnets = ec2.describe_subnets(Filters=[{
        'Name': 'vpc-id',
        'Values': [dc_id]}])
    route_tables = ec2.describe_route_tables(Filters=[{
        'Name': 'vpc-id',
        'Values': [dc_id]}])
    assert len(route_tables["RouteTables"]) == 7
    assert len(subnets["Subnets"]) == 6

    # AutoScalingGroup
    autoscaling = boto3.client("autoscaling")
    asgs = autoscaling.describe_auto_scaling_groups(
            AutoScalingGroupNames=["unittest.web"])
    assert len(asgs["AutoScalingGroups"]) == 1
    asg = asgs["AutoScalingGroups"][0]
    assert asg["AutoScalingGroupName"] == "unittest.web"
    assert asg["LaunchConfigurationName"] == "unittest.web"
    assert len(asg["LoadBalancerNames"]) == 1
    assert asg["LoadBalancerNames"][0] == "unittest-web-lb"
    assert web.discover("unittest.web")["Id"] == "unittest.web"

    # Load Balancer
    elb = boto3.client("elb")
    load_balancers = [load_balancer for load_balancer in
                      elb.describe_load_balancers()["LoadBalancerDescriptions"]
                      if load_balancer["LoadBalancerName"] == "unittest-web-lb"
                      ]
    assert len(load_balancers) == 1
    assert load_balancers[0]["LoadBalancerName"] == "unittest-web-lb"

    # DNS
    route53 = boto3.client("route53")
    hosted_zones = route53.list_hosted_zones()
    assert len(hosted_zones["HostedZones"]) == 1
    assert hosted_zones["HostedZones"][0]["Name"] == "myexamplesite.com."
    zone_id = hosted_zones["HostedZones"][0]["Id"].split("/")[-1]
    resource_record_sets = route53.list_resource_record_sets(
            HostedZoneId=zone_id)
    lb_dns = load_balancers[0]["DNSName"]
    for resource_record_set in resource_record_sets["ResourceRecordSets"]:
        if resource_record_set["Type"] == "CNAME":
            # Need this because moto appends a dot but boto doesn't
            assert resource_record_set["Name"].startswith(
                    "foo.myexamplesite.com")
            assert len(resource_record_set["ResourceRecords"]) == 1
            assert resource_record_set["ResourceRecords"][0]["Value"] == lb_dns

    # Make sure subnets don't overlap
    asg = asgs["AutoScalingGroups"][0]
    asg_subnet_ids = asg["VPCZoneIdentifier"].split(",")
    assert len(asg_subnet_ids) == 3

    load_balancer_subnet_ids = load_balancers[0]["Subnets"]
    assert len(load_balancer_subnet_ids) == 3

    asg_subnets = ec2.describe_subnets(SubnetIds=asg_subnet_ids)
    assert len(asg_subnets["Subnets"]) == 3

    load_balancer_subnets = ec2.describe_subnets(
            SubnetIds=load_balancer_subnet_ids)
    assert len(load_balancer_subnets["Subnets"]) == 3

    for asg_subnet in asg_subnets["Subnets"]:
        asg_cidr = ipaddress.ip_network(unicode(asg_subnet["CidrBlock"]))
        for load_balancer_subnet in load_balancer_subnets["Subnets"]:
            load_balancer_cidr = ipaddress.ip_network(
                    unicode(load_balancer_subnet["CidrBlock"]))
            assert not asg_cidr.overlaps(load_balancer_cidr)

    # Make sure they got allocated in the same VPC
    asg_vpc_id = None
    for asg_subnet in asg_subnets["Subnets"]:
        if not asg_vpc_id:
            asg_vpc_id = asg_subnet["VpcId"]
        assert asg_subnet["VpcId"] == asg_vpc_id

    load_balancer_vpc_id = None
    for load_balancer_subnet in load_balancer_subnets["Subnets"]:
        if not load_balancer_vpc_id:
            load_balancer_vpc_id = load_balancer_subnet["VpcId"]
        assert load_balancer_subnet["VpcId"] == load_balancer_vpc_id

    assert asg_vpc_id == load_balancer_vpc_id

    # Give the ASG time to spin up some instances
    time.sleep(15)

    # Make sure they are gone when I destroy them
    lb.destroy("unittest.web-lb")
    dns.destroy("foo.myexamplesite.com")

    # Networking
    ec2 = boto3.client("ec2")
    subnets = ec2.describe_subnets(Filters=[{
        'Name': 'vpc-id',
        'Values': [dc_id]}])
    route_tables = ec2.describe_route_tables(Filters=[{
        'Name': 'vpc-id',
        'Values': [dc_id]}])
    assert len(route_tables["RouteTables"]) == 4
    assert len(subnets["Subnets"]) == 3

    # AutoScalingGroup
    autoscaling = boto3.client("autoscaling")
    asgs = autoscaling.describe_auto_scaling_groups(
            AutoScalingGroupNames=["unittest.web"])
    assert len(asgs["AutoScalingGroups"]) == 1
    asg = asgs["AutoScalingGroups"][0]
    assert asg["AutoScalingGroupName"] == "unittest.web"
    assert asg["LaunchConfigurationName"] == "unittest.web"
    assert len(asg["LoadBalancerNames"]) == 1
    assert asg["LoadBalancerNames"][0] == "unittest-web-lb"

    # Load Balancer
    elb = boto3.client("elb")
    load_balancers = [load_balancer for load_balancer in
                      elb.describe_load_balancers()["LoadBalancerDescriptions"]
                      if load_balancer["LoadBalancerName"] == "unittest-web-lb"
                      ]
    assert len(load_balancers) == 0

    # DNS
    route53 = boto3.client("route53")
    hosted_zones = route53.list_hosted_zones()
    assert len(hosted_zones["HostedZones"]) == 0

    # Now destroy the rest
    web.destroy("unittest.web")

    # Give things time to clear in AWS (because apparently things return before
    # they are actually gone???).
    # XXX: FIXME: This is bad and the library itself should actually wait.
    time.sleep(60)

    # AutoScalingGroup
    autoscaling = boto3.client("autoscaling")
    asgs = autoscaling.describe_auto_scaling_groups(
            AutoScalingGroupNames=["unittest.web"])
    assert len(asgs["AutoScalingGroups"]) == 0

    # Load Balancer
    elb = boto3.client("elb")
    load_balancers = [load_balancer for load_balancer in
                      elb.describe_load_balancers()["LoadBalancerDescriptions"]
                      if load_balancer["LoadBalancerName"] == "unittest-web-lb"
                      ]
    assert len(load_balancers) == 0

    # DNS
    route53 = boto3.client("route53")
    hosted_zones = route53.list_hosted_zones()
    assert len(hosted_zones["HostedZones"]) == 0


@mock_ec2
@mock_elb
@mock_autoscaling
@mock_route53
@pytest.mark.mock
def test_service_mock():
    run_service_test()


@pytest.mark.real
def test_service_real():
    run_service_test()

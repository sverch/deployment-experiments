import boto3
from moto import mock_ec2, mock_autoscaling, mock_elb, mock_route53
from deployment_experiments.service import LoadBalancer, Service
from deployment_experiments.datacenter import DatacenterInventory
from deployment_experiments.virtual_machine import VirtualMachine
from deployment_experiments.virtual_machine import VirtualMachinePlugin
import ipaddress


@mock_ec2
@mock_elb
@mock_autoscaling
@mock_route53
def test_service():
    nginx = VirtualMachinePlugin(
            "https://github.com/cloud-deployer/plugins/nginx-build",
            "https://github.com/cloud-deployer/plugins/nginx-runtime")
    splunk = VirtualMachinePlugin(
            "https://github.com/cloud-deployer/plugins/splunk-build",
            "https://github.com/cloud-deployer/plugins/splunk-runtime")
    newrelic = VirtualMachinePlugin(
            "https://github.com/cloud-deployer/plugins/newrelic-build",
            "https://github.com/cloud-deployer/plugins/newrelic-runtime")
    image = VirtualMachine(plugins=[nginx, splunk, newrelic])
    lb = LoadBalancer("web-lb", "foo.example.com")
    web = Service("web", image, lb)
    lb.add_path(target=web, port=80, intermediates=[])
    lb.provision()
    web.provision(colocated_service=lb.name)

    # Try to get them from the DC inventory
    dc_inventory = DatacenterInventory()
    dc_ids = dc_inventory.discover()
    assert len(dc_ids) == 1

    # Networking
    ec2 = boto3.client("ec2")
    subnets = ec2.describe_subnets(Filters=[{
        'Name': 'vpc-id',
        'Values': [dc_ids[0]]}])
    route_tables = ec2.describe_route_tables(Filters=[{
        'Name': 'vpc-id',
        'Values': [dc_ids[0]]}])
    assert len(route_tables["RouteTables"]) == 1
    route_table = route_tables["RouteTables"][0]
    assert route_table["Associations"] == []
    assert len(route_table["Routes"]) == 1
    assert route_table["Routes"][0]["DestinationCidrBlock"] == "10.0.0.0/16"
    assert route_table["Associations"] == []
    assert len(subnets["Subnets"]) == 6

    # AutoScalingGroup
    autoscaling = boto3.client("autoscaling")
    asgs = autoscaling.describe_auto_scaling_groups(
            AutoScalingGroupNames=["web"])
    assert len(asgs["AutoScalingGroups"]) == 1
    assert asgs["AutoScalingGroups"][0]["AutoScalingGroupName"] == "web"
    assert asgs["AutoScalingGroups"][0]["LaunchConfigurationName"] == "web"
    assert len(asgs["AutoScalingGroups"][0]["LoadBalancerNames"]) == 1
    assert asgs["AutoScalingGroups"][0]["LoadBalancerNames"][0] == "web-lb"

    # Load Balancer
    elb = boto3.client("elb")
    load_balancers = [load_balancer for load_balancer in
                      elb.describe_load_balancers()["LoadBalancerDescriptions"]
                      if load_balancer["LoadBalancerName"] == "web-lb"]
    assert len(load_balancers) == 1
    assert load_balancers[0]["LoadBalancerName"] == "web-lb"
    lb_dns = load_balancers[0]["DNSName"]

    # DNS
    route53 = boto3.client("route53")
    hosted_zones = route53.list_hosted_zones()
    assert len(hosted_zones["HostedZones"]) == 1
    assert hosted_zones["HostedZones"][0]["Name"] == "example.com."
    zone_id = hosted_zones["HostedZones"][0]["Id"].split("/")[-1]
    resource_record_sets = route53.list_resource_record_sets(
            HostedZoneId=zone_id)
    assert len(resource_record_sets["ResourceRecordSets"]) == 1
    resource_record_set = resource_record_sets["ResourceRecordSets"][0]
    assert resource_record_set["Name"] == "foo.example.com"
    assert resource_record_set["Type"] == "CNAME"
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

    # Make sure they are gone when I destroy them
    lb.destroy()

    # DC
    dc_inventory = DatacenterInventory()
    dc_ids = dc_inventory.discover()
    assert len(dc_ids) == 1

    # Networking
    ec2 = boto3.client("ec2")
    subnets = ec2.describe_subnets(Filters=[{
        'Name': 'vpc-id',
        'Values': [dc_ids[0]]}])
    route_tables = ec2.describe_route_tables(Filters=[{
        'Name': 'vpc-id',
        'Values': [dc_ids[0]]}])
    assert len(route_tables["RouteTables"]) == 1
    route_table = route_tables["RouteTables"][0]
    assert route_table["Associations"] == []
    assert len(route_table["Routes"]) == 1
    assert route_table["Routes"][0]["DestinationCidrBlock"] == "10.0.0.0/16"
    assert route_table["Associations"] == []
    assert len(subnets["Subnets"]) == 3

    # AutoScalingGroup
    autoscaling = boto3.client("autoscaling")
    asgs = autoscaling.describe_auto_scaling_groups(
            AutoScalingGroupNames=["web"])
    assert len(asgs["AutoScalingGroups"]) == 1
    assert asgs["AutoScalingGroups"][0]["AutoScalingGroupName"] == "web"
    assert asgs["AutoScalingGroups"][0]["LaunchConfigurationName"] == "web"
    assert len(asgs["AutoScalingGroups"][0]["LoadBalancerNames"]) == 1
    assert asgs["AutoScalingGroups"][0]["LoadBalancerNames"][0] == "web-lb"

    # Load Balancer
    elb = boto3.client("elb")
    load_balancers = [load_balancer for load_balancer in
                      elb.describe_load_balancers()["LoadBalancerDescriptions"]
                      if load_balancer["LoadBalancerName"] == "web-lb"]
    assert len(load_balancers) == 0

    # DNS
    route53 = boto3.client("route53")
    hosted_zones = route53.list_hosted_zones()
    assert len(hosted_zones["HostedZones"]) == 0

    # Now destroy the rest
    web.destroy()

    # DC
    dc_inventory = DatacenterInventory()
    dc_ids = dc_inventory.discover()
    assert len(dc_ids) == 0

    # AutoScalingGroup
    autoscaling = boto3.client("autoscaling")
    asgs = autoscaling.describe_auto_scaling_groups(
            AutoScalingGroupNames=["web"])
    assert len(asgs["AutoScalingGroups"]) == 0

    # Load Balancer
    elb = boto3.client("elb")
    load_balancers = [load_balancer for load_balancer in
                      elb.describe_load_balancers()["LoadBalancerDescriptions"]
                      if load_balancer["LoadBalancerName"] == "web-lb"]
    assert len(load_balancers) == 0

    # DNS
    route53 = boto3.client("route53")
    hosted_zones = route53.list_hosted_zones()
    assert len(hosted_zones["HostedZones"]) == 0

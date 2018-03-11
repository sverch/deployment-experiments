#!/usr/bin/env python

import attr
import boto3

from instance_fitter import InstanceFitter

from network import Network
from datacenter import Datacenter
from exceptions import OperationTimedOut
from exceptions import BadEnvironmentStateException

import uuid
import time


@attr.s
class ServiceDns(object):
    provider = attr.ib(default="aws")

    def create_zone(self, dns):
        route53 = boto3.client("route53")
        # https://stackoverflow.com/questions/34644483/why-do-i-have-to-change-the-callerreference-on-every-call
        caller_reference = str(uuid.uuid4())
        zone_name = ".".join(dns.split(".")[1:])
        return route53.create_hosted_zone(
                Name=zone_name, CallerReference=caller_reference)

    def provision(self, dns, target):
        route53 = boto3.client("route53")
        zone = self.create_zone(dns)
        change_batch = [
                {
                    "Action": "CREATE",
                    "ResourceRecordSet": {
                        "Name": dns,
                        "Type": "CNAME",
                        "ResourceRecords": [
                            {"Value": target}
                        ],
                        "TTL": 60
                    }
                }
            ]
        route53.change_resource_record_sets(
                HostedZoneId=zone["HostedZone"]["Id"],
                ChangeBatch={
                    "Comment": "Creating basic service DNS",
                    "Changes": change_batch
                    })

    def discover(self, dns):
        route53 = boto3.client("route53")
        zone_name = ".".join(dns.split(".")[1:])
        hosted_zones = route53.list_hosted_zones_by_name(
                DNSName="%s." % zone_name)
        hosted_zone_ids = [zone["Id"] for zone in hosted_zones["HostedZones"]]
        return hosted_zone_ids

    def destroy(self, dns):
        zone_ids = self.discover(dns)
        route53 = boto3.client("route53")
        for zone_id in zone_ids:
            rr_sets = route53.list_resource_record_sets(
                    HostedZoneId=zone_id)["ResourceRecordSets"]
            for rr_set in rr_sets:
                if rr_set["Type"] in ["NS", "SOA"]:
                    continue
                change_batch = [
                        {
                            "Action": "DELETE",
                            "ResourceRecordSet": rr_set
                        }
                    ]
                route53.change_resource_record_sets(
                        HostedZoneId=zone_id,
                        ChangeBatch={
                            "Comment": "Deleting basic service DNS",
                            "Changes": change_batch
                            })
            route53.delete_hosted_zone(Id=zone_id)


@attr.s
class LoadBalancer(object):
    """
    A load balancer object.
    """
    provider = attr.ib(default="aws")
    retry_count = attr.ib(default="60")
    retry_delay = attr.ib(default="1.0")

    def parse_name(self, name):
        """
        Given a full network name, return (vpc_name, network_name).
        """
        return tuple(name.split("."))

    def private_security_groups(self, name, vpc_id):
        ec2 = boto3.client("ec2")
        group = ec2.create_security_group(VpcId=vpc_id,
                                          GroupName=name,
                                          Description=name)
        return [group["GroupId"]]

    def aws_provision(self, name):
        elb = boto3.client("elb")
        listeners = [
                {
                    'Protocol': 'http',
                    'LoadBalancerPort': 80,
                    'InstanceProtocol': 'http',
                    'InstancePort': 80
                    }
                ]
        net = Network()
        subnet_ids = [subnet_info["Id"] for subnet_info
                      in net.provision(name)]
        # XXX: Right now I need this here because an internet gateway needs to
        # exist for the load balancer to be provisioned.  That probably means I
        # should just provision the internet gateway with the VPC, especially
        # since there's only one.
        net.expose(name)
        vpc_name, network_name = self.parse_name(name)
        dc = Datacenter()
        vpc_id = dc.discover(vpc_name)["Id"]
        load_balancer = elb.create_load_balancer(
                LoadBalancerName=self.lb_name(name),
                Listeners=listeners,
                Subnets=subnet_ids,
                SecurityGroups=self.private_security_groups(name, vpc_id))
        return self.canonicalize_load_balancer(self.lb_name(name),
                                               load_balancer["DNSName"])

    def lb_name(self, name):
        return name.replace(".", "-")

    def canonicalize_load_balancer(self, load_balancer_name,
                                   load_balancer_dns):
        return {
                "Id": load_balancer_name,
                "DNSName": load_balancer_dns
                }

    def aws_describe_load_balancer(self, name):
        elb = boto3.client("elb")
        try:
            load_balancers = elb.describe_load_balancers(
                    LoadBalancerNames=[self.lb_name(name)])
        except elb.exceptions.AccessPointNotFoundException, e:
            if e.response['Error']['Code'] == 'LoadBalancerNotFound':
                return None
            else:
                raise e
        if len(load_balancers["LoadBalancerDescriptions"]) > 1:
            raise BadEnvironmentStateException(
                    "Expected to find at most one load_balancer named: %s, "
                    "output: %s" % (name, load_balancers))
        if len(load_balancers["LoadBalancerDescriptions"]) == 0:
            return None
        return load_balancers["LoadBalancerDescriptions"][0]

    def aws_discover(self, name):
        load_balancer = self.aws_describe_load_balancer(name)
        if load_balancer:
            return self.canonicalize_load_balancer(
                    load_balancer["LoadBalancerName"],
                    load_balancer["DNSName"])
        else:
            return None

    def provision(self, name):
        if self.provider == "aws":
            self.aws_provision(name)
        else:
            raise NotImplemented

    def discover(self, name):
        if self.provider == "aws":
            return self.aws_discover(name)
        else:
            raise NotImplemented

    def expose(self, name):
        """
        Make this load balancer open to the internet.

        This involves opening up the security group and exposing the network
        layer which will route through the internet gateway.
        """
        load_balancer = self.aws_describe_load_balancer(name)
        security_groups = load_balancer["SecurityGroups"]
        if len(security_groups) != 1:
            raise BadEnvironmentStateException("Expected load balancer %s "
                                               "to have exactly one "
                                               "security group: %s",
                                               name, load_balancer)
        ec2 = boto3.client("ec2")
        security_group_id = security_groups[0]
        ec2.authorize_security_group_ingress(GroupId=security_group_id,
                                             CidrIp="0.0.0.0/0",
                                             IpProtocol="-1")

    def destroy(self, name):
        elb = boto3.client("elb")
        elb.delete_load_balancer(LoadBalancerName=self.lb_name(name))

        # Wait for the load balancer to actually disappear
        deletion_retries = 0
        while deletion_retries < self.retry_count:
            if self.discover(name):
                time.sleep(float(self.retry_delay))
                continue
            else:
                break

        net = Network()
        net.destroy(name)


@attr.s
class NatGatewayService(object):
    """
    Service to create a NAT gateway, since in AWS it is special.
    """
    provider = attr.ib(default="aws")
    retry_count = attr.ib(default="60")
    retry_delay = attr.ib(default="1.0")

    def parse_name(self, name):
        """
        Given a full network name, return (vpc_name, network_name).
        """
        # TODO: Throw error if it's not
        # "vpc_name.service_name" format.
        return tuple(name.split("."))

    def get_instance_type(self):
        # TODO: Actually allow for passing in these values to find the cheapest
        # fitting instance.
        instance_fitter = InstanceFitter()
        return instance_fitter.get_fitting_instance(memory=None, cpus=None,
                                                    storage=None)

    def private_security_groups(self, name, vpc_id):
        ec2 = boto3.client("ec2")
        group = ec2.create_security_group(VpcId=vpc_id,
                                          GroupName=name,
                                          Description=name)
        ec2.authorize_security_group_ingress(GroupId=group["GroupId"],
                                             CidrIp="0.0.0.0/0",
                                             IpProtocol="-1")
        return [group["GroupId"]]

    def aws_provision_nat_gateway(self, name, subnet_id):
        ec2 = boto3.client("ec2")
        vpc_name, network_name = self.parse_name(name)
        allocation = ec2.allocate_address(Domain="vpc")
        nat_gateway = ec2.create_nat_gateway(
            AllocationId=allocation["AllocationId"],
            # TODO: Better retries.  See
            # https://docs.aws.amazon.com/AWSEC2/latest/APIReference/Run_Instance_Idempotency.html.
            ClientToken=subnet_id,
            SubnetId=subnet_id
        )
        nat_gateway_id = nat_gateway["NatGateway"]["NatGatewayId"]
        try:
            creation_retries = 0
            while creation_retries < self.retry_count:
                try:
                    ec2.create_tags(
                            Resources=[nat_gateway_id],
                            Tags=[{"Key": "tag:cloud-deployer-deployment",
                                   "Value": vpc_name},
                                  {"Key": "tag:cloud-deployer-network",
                                   "Value": network_name}])
                    if nat_gateway_id not in self.aws_discover(name):
                        time.sleep(float(self.retry_delay))
                    else:
                        break
                except Exception:
                    time.sleep(float(self.retry_delay))
                    creation_retries = creation_retries + 1
                    if creation_retries >= self.retry_count:
                        raise OperationTimedOut(
                                "Cannot find created Subnet: %s" % subnet_id)
        except Exception, e:
            ec2.delete_subnet(SubnetId=subnet_id)
            raise e

    def aws_provision(self, name):
        net = Network()
        subnet_ids = [subnet_info["Id"] for subnet_info
                      in net.provision(name)]
        for subnet_id in subnet_ids:
            self.aws_provision_nat_gateway(name, subnet_id)

    def aws_discover(self, name):
        ec2 = boto3.client("ec2")
        vpc_name, network_name = self.parse_name(name)
        filters = [{"Name": "tag:cloud-deployer-deployment",
                    "Values": [vpc_name]},
                   {"Name": "tag:cloud-deployer-network",
                    "Values": [network_name]}]
        nat_gateways = ec2.describe_nat_gateways(Filters=filters)
        nat_gateway_ids = [nat_gateway["NatGatewayId"] for nat_gateway
                           in nat_gateways["NatGateways"]]
        return nat_gateway_ids

    def provision(self, name):
        if self.provider == "aws":
            self.aws_provision(name)
        else:
            raise NotImplemented

    def discover(self, name):
        if self.provider == "aws":
            return self.aws_discover(name)
        else:
            raise NotImplemented

    def destroy(self, name):
        ec2 = boto3.client("ec2")
        for nat_gateway_id in self.discover(name):
            ec2.delete_nat_gateway(NatGatewayId=nat_gateway_id)
        net = Network()
        net.destroy(name)


@attr.s
class Service(object):
    """
    Represents a group of instances.
    """
    provider = attr.ib(default="aws")
    retry_count = attr.ib(default="60")
    retry_delay = attr.ib(default="1.0")

    def parse_name(self, name):
        """
        Given a full network name, return (vpc_name, network_name).
        """
        # TODO: Throw error if it's not
        # "vpc_name.service_name" format.
        return tuple(name.split("."))

    def find_ami(self, image):
        return image.get()

    def get_instance_type(self):
        # TODO: Actually allow for passing in these values to find the cheapest
        # fitting instance.
        instance_fitter = InstanceFitter()
        return instance_fitter.get_fitting_instance(memory=None, cpus=None,
                                                    storage=None)

    def private_security_groups(self, name, vpc_id):
        ec2 = boto3.client("ec2")
        group = ec2.create_security_group(VpcId=vpc_id,
                                          GroupName=name,
                                          Description=name)
        return [group["GroupId"]]

    def describe_launch_configuration(self, name):
        autoscaling = boto3.client("autoscaling")
        launch_configurations = autoscaling.describe_launch_configurations(
                LaunchConfigurationNames=[name])
        # TODO: Return None here instead?
        if len(launch_configurations["LaunchConfigurations"]) != 1:
            raise BadEnvironmentStateException("Expected one launch "
                                               "configuration with name "
                                               "%s: %s", name,
                                               launch_configurations)
        return launch_configurations["LaunchConfigurations"][0]

    def allow(self, name, source):
        launch_configuration = self.describe_launch_configuration(name)
        security_groups = launch_configuration["SecurityGroups"]
        if len(security_groups) != 1:
            raise BadEnvironmentStateException("Expected launch configuration "
                                               "%s to have exactly one "
                                               "security group: %s",
                                               name, launch_configuration)
        ec2 = boto3.client("ec2")
        security_group_id = security_groups[0]
        # TODO: Actually discover the other security group based on name and
        # add it explicitly here.
        ec2.authorize_security_group_ingress(GroupId=security_group_id,
                                             CidrIp="0.0.0.0/0",
                                             IpProtocol="-1")

    def launch_configuration(self, name, image):
        autoscaling = boto3.client("autoscaling")
        user_data = image.get_runtime_scripts()
        vpc_name, network_name = self.parse_name(name)
        dc = Datacenter()
        vpc_id = dc.discover(vpc_name)["Id"]
        return autoscaling.create_launch_configuration(
                LaunchConfigurationName=name,
                ImageId=self.find_ami(image),
                # See https://github.com/hashicorp/terraform/issues/3600
                SecurityGroups=self.private_security_groups(name, vpc_id),
                UserData=user_data,
                # Just do this for POC until I get real NAT
                AssociatePublicIpAddress=True,
                InstanceType=self.get_instance_type())

    def auto_scaling_group(self, name, subnets, load_balancer, image):
        autoscaling = boto3.client("autoscaling")
        comma_separated_subnets = ",".join(subnets)
        self.launch_configuration(name, image)
        return autoscaling.create_auto_scaling_group(
                AutoScalingGroupName=name,
                LaunchConfigurationName=name,
                MinSize=3,
                MaxSize=3,
                DesiredCapacity=3,
                VPCZoneIdentifier=comma_separated_subnets,
                LoadBalancerNames=[load_balancer],
                HealthCheckType='ELB',
                HealthCheckGracePeriod=120)

    def aws_provision(self, name, load_balancer, image):
        net = Network()
        subnet_ids = [subnet_info["Id"] for subnet_info
                      in net.provision(name)]
        # XXX: Right now I need this because I don't have NAT.  TODO: Implement
        # one way but not the other?  Expose currently implements both
        # directions.
        net.expose(name)
        self.auto_scaling_group(name, subnet_ids, load_balancer, image)
        return self.canonicalize_auto_scaling_group(name)

    def canonicalize_auto_scaling_group(self, name):
        return {"Id": name}

    def aws_discover(self, name):
        autoscaling = boto3.client("autoscaling")
        asgs = autoscaling.describe_auto_scaling_groups(
                AutoScalingGroupNames=[name])
        if len(asgs["AutoScalingGroups"]) > 1:
            raise BadEnvironmentStateException(
                    "Expected to find at most one auto scaling group "
                    "named: %s, output: %s" % (name, asgs))
        if len(asgs["AutoScalingGroups"]) == 0:
            return None
        asg = asgs["AutoScalingGroups"][0]
        return self.canonicalize_auto_scaling_group(
                asg["AutoScalingGroupName"])

    def provision(self, name, load_balancer, image):
        if self.provider == "aws":
            self.aws_provision(name, load_balancer, image)
        else:
            raise NotImplemented

    def discover(self, name):
        if self.provider == "aws":
            return self.aws_discover(name)
        else:
            raise NotImplemented

    def destroy(self, name):
        autoscaling = boto3.client("autoscaling")
        ec2 = boto3.client("ec2")
        # FIXME: Catch exceptions from these, and probably just continue
        # deleting the other parts of this service if they're already gone.
        autoscaling.update_auto_scaling_group(AutoScalingGroupName=name,
                                              MinSize=0, DesiredCapacity=0)
        autoscaling.delete_auto_scaling_group(AutoScalingGroupName=name,
                                              ForceDelete=True)
        autoscaling.delete_launch_configuration(
                LaunchConfigurationName=name)
        net = Network()
        vpc_name, _ = self.parse_name(name)
        dc = Datacenter()
        vpc_id = dc.discover(vpc_name)["Id"]
        # FIXME: Actually only delete the security group attached to this auto
        # scaling group, don't delete them all....
        security_groups = ec2.describe_security_groups(
                Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}])
        for security_group in security_groups["SecurityGroups"]:
            if security_group["GroupName"] != "default":
                deletion_retries = 0
                while deletion_retries < self.retry_count:
                    try:
                        ec2.delete_security_group(
                                GroupId=security_group["GroupId"])
                        break
                    except Exception, e:
                        deletion_retries = deletion_retries + 1
                        if deletion_retries >= self.retry_count:
                            raise e
                        time.sleep(float(self.retry_delay))
        net.destroy(name)

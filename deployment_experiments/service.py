#!/usr/bin/env python

import attr
import boto3

from subnet_generator import generate_subnets
from instance_fitter import InstanceFitter

from network import Network

import uuid

@attr.s
class ServiceDns(object):
    dns = attr.ib()
    target = attr.ib()
    provider = attr.ib(default="aws")

    def create_zone(self):
        route53 = boto3.client("route53")
        # https://stackoverflow.com/questions/34644483/why-do-i-have-to-change-the-callerreference-on-every-call
        caller_reference = str(uuid.uuid4())
        zone_name = ".".join(self.dns.split(".")[1:])
        return route53.create_hosted_zone(Name=zone_name, CallerReference=caller_reference)

    def provision(self):
        route53 = boto3.client("route53")
        zone = self.create_zone()
        change_batch = [
                {
                    "Action": "CREATE",
                    "ResourceRecordSet": {
                        "Name": self.dns,
                        "Type": "CNAME",
                        "ResourceRecords": [
                            { "Value": self.target }
                        ],
                        "TTL": 60
                    }
                }
            ]
        route53.change_resource_record_sets(HostedZoneId=zone["HostedZone"]["Id"],
                                            ChangeBatch={
                                                "Comment": "Creating basic service DNS",
                                                "Changes": change_batch
                                                })

    def discover(self):
        route53 = boto3.client("route53")
        zone_name = ".".join(self.dns.split(".")[1:])
        hosted_zones = route53.list_hosted_zones_by_name(DNSName="%s." % zone_name)
        hosted_zone_ids = [zone["Id"] for zone in hosted_zones["HostedZones"]]
        return hosted_zone_ids

    def destroy(self):
        zone_ids = self.discover()
        route53 = boto3.client("route53")
        for zone_id in zone_ids:
            rr_sets = route53.list_resource_record_sets(HostedZoneId=zone_id)["ResourceRecordSets"]
            for rr_set in rr_sets:
                if rr_set["Type"] in ["NS", "SOA"]:
                    continue
                change_batch = [
                        {
                            "Action": "DELETE",
                            "ResourceRecordSet": rr_set
                        }
                    ]
                route53.change_resource_record_sets(HostedZoneId=zone_id,
                                                    ChangeBatch={
                                                      "Comment": "Deleting basic service DNS",
                                                      "Changes": change_batch
                                                      })
            route53.delete_hosted_zone(Id=zone_id)

@attr.s
class LoadBalancer(object):
    """
    A load balancer object.

    Balances load across the actual services.  Still working on getting this
    interface right.  A LoadBalancer kind of has special properties in some
    ways...  I don't know if it should necessarily, but you interact with it in
    a different way in AWS.

    So in AWS, I create the load balancer, which has a DNS name that gets
    updated with the correct instances automagically.  This is something that
    the autoscaling group needs in AWS as well.

    This could actually be above the service in the "abstractions" model.  Or
    perhaps it is a subservice?
    """
    name = attr.ib()
    dns = attr.ib()
    provider = attr.ib(default="aws")

    def aws_provision(self):
        elb = boto3.client("elb")
        ec2 = boto3.client("ec2")
        listeners = [
                {
                    'Protocol': 'http',
                    'LoadBalancerPort': 80,
                    'InstanceProtocol': 'http',
                    'InstancePort': 80
                    }
                ]
        net = Network()
        subnet_ids = net.provision(network_name=self.name)
        load_balancer = elb.create_load_balancer(LoadBalancerName=self.name,
                                                 Listeners=listeners,
                                                 Subnets=subnet_ids)
        dns = ServiceDns(self.dns, load_balancer["DNSName"])
        dns.provision()

    def aws_discover(self):
        # TODO: I think this throws an exception, but figure out proper error
        # handling.
        elb = boto3.client("elb")
        return elb.describe_load_balancers(LoadBalancerNames=[self.name])

    def provision(self):
        if self.provider == "aws":
            self.aws_provision()
        else:
            raise NotImplemented

    def discover(self):
        if self.provider == "aws":
            return self.aws_discover()
        else:
            raise NotImplemented

    def add_path(self, target, port, intermediates):
        # TODO: This is how I'll set up routing/firewall rules
        pass

    def destroy(self):
        elb = boto3.client("elb")
        elb.delete_load_balancer(LoadBalancerName=self.name)
        dns = ServiceDns(self.dns, "dummy")
        dns.destroy()
        net = Network()
        net.destroy(network_name=self.name)

@attr.s
class Service(object):
    """
    A service object.

    Creates the actual service instances.  Does not do anything with networking or anything like that.
    """
    name = attr.ib()
    image = attr.ib()
    load_balancer = attr.ib()
    provider = attr.ib(default="aws")

    def find_ami(self):
        return self.image.get()

    def get_instance_type(self):
        # TODO: Actually allow for passing in these values to find the cheapest
        # fitting instance.
        instance_fitter = InstanceFitter()
        return instance_fitter.get_fitting_instance(memory=None, cpus=None, storage=None)

    def launch_configuration(self, name):
        autoscaling = boto3.client("autoscaling")
        user_data = self.image.build_cloud_init()
        return autoscaling.create_launch_configuration(
                LaunchConfigurationName=name,
                ImageId=self.find_ami(),
                # See https://github.com/hashicorp/terraform/issues/3600
                #SecurityGroups=self.private_security_groups(),
                UserData=user_data,
                InstanceType=self.get_instance_type())

    def auto_scaling_group(self, name, subnets):
        autoscaling = boto3.client("autoscaling")
        comma_separated_subnets = ",".join(subnets)
        launch_configuration = self.launch_configuration(name)
        load_balancers = self.load_balancer.discover()
        load_balancer_names = [load_balancer["LoadBalancerName"]
                               for load_balancer in load_balancers["LoadBalancerDescriptions"]]
        return autoscaling.create_auto_scaling_group(
                AutoScalingGroupName=name,
                LaunchConfigurationName=name,
                MinSize=3,
                MaxSize=3,
                DesiredCapacity=3,
                VPCZoneIdentifier=comma_separated_subnets,
                LoadBalancerNames=load_balancer_names,
                HealthCheckType='ELB',
                HealthCheckGracePeriod=120)

    def aws_provision(self, colocated_service):
        net = Network()
        subnet_ids = net.provision(colocated_network=colocated_service, network_name=self.name)
        self.auto_scaling_group(self.name, subnet_ids)

    def aws_discover(self):
        autoscaling = boto3.client("autoscaling")
        name_filter = {'Name': "tag:deploy-name", 'Values': [self.name]}
        return autoscaling.describe_auto_scaling_groups(Filters=[name_filter])

    def provision(self, colocated_service=None):
        if self.provider == "aws":
            self.aws_provision(colocated_service)
        else:
            raise NotImplemented

    def discover(self):
        if self.provider == "aws":
            return self.aws_discover()
        else:
            raise NotImplemented

    def destroy(self):
        autoscaling = boto3.client("autoscaling")
        autoscaling.delete_auto_scaling_group(AutoScalingGroupName=self.name)
        autoscaling.delete_launch_configuration(LaunchConfigurationName=self.name)
        net = Network()
        net.destroy(network_name=self.name)

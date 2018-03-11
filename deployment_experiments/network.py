#!/usr/bin/env python

import attr
import boto3

from subnet_generator import generate_subnets
from datacenter import Datacenter


class NotEnoughIPSpaceException(Exception):
    pass


@attr.s
class Network(object):
    """
    A network object.

    This is a carved out section of a datacenter.  Should automatically spread
    across avialiability zones.

    There is absolutely no need to discover networks based on tag, or any
    reason to tag them.  They are associated with a service and contained in a
    VPC, and can be discovered both ways.

    So how should they be allocated?

    I guess it's just.

    Well, I do want to easily see which subnets are for which service.

    So let's tag based on service.

    This is also slightly different from the VPC, because it's not one thing.
    If I give a different list of IDs, I will discover different things.
    Perhaps I should discover based on service name and datacenter id, if it's
    set.
    """
    provider = attr.ib(default="aws")
    deployment_name = attr.ib(default="default")

    def carve_subnets(self, vpc_id, prefix=28, count=3):
        # First, grab the vpc_cidr using the VPC id
        ec2 = boto3.client("ec2")
        vpc = ec2.describe_vpcs(VpcIds=[vpc_id])
        vpc_cidr = vpc["Vpcs"][0]["CidrBlock"]

        # Then, get existing subnets, to make sure we don't overlap CIDR blocks
        existing_subnets = ec2.describe_subnets(Filters=[{
                'Name': 'vpc-id',
                'Values': [vpc_id]}])
        existing_cidrs = [subnet["CidrBlock"]
                          for subnet in existing_subnets["Subnets"]]

        # Finally, iterate the list of all subnets of the given prefix that can
        # fit in the given VPC
        subnets = []
        for new_cidr in generate_subnets(vpc_cidr, existing_cidrs, prefix):
            subnets.append(str(new_cidr))
            if len(subnets) == count:
                return subnets

        # TODO: Better error handling
        raise NotEnoughIPSpaceException("Could not allocate %s subnets with "
                                        "prefix %s in vpc %s",
                                        (count, prefix, vpc_id))

    def get_availability_zones(self):
        # TODO: XXX: Moto does not have this function supported...  So I need
        # to fix that before this code can be reasonable again, because
        # otherwise it just fails.
        ec2 = boto3.client("ec2")
        try:
            availability_zones = ec2.describe_availablity_zones()
            return [az["ZoneName"]
                    for az in availability_zones["AvailabilityZones"]]
        except Exception:
            return ["us-east-1a", "us-east-1b", "us-east-1c"]

    def aws_provision(self, colocated_network, network_name):
        ec2 = boto3.client("ec2")
        dc_id = None
        if not colocated_network:
            dc = Datacenter(deployment_name=self.deployment_name)
            dc_id = dc.create()
        else:
            dc_id = None
            subnet_ids = self.discover(colocated_network)
            subnets = ec2.describe_subnets(SubnetIds=subnet_ids)
            for subnet in subnets["Subnets"]:
                if not dc_id:
                    dc_id = subnet["VpcId"]
                assert subnet["VpcId"] == dc_id
        subnet_ids = []
        availability_zones = self.get_availability_zones()
        for subnet_cidr, availability_zone in zip(self.carve_subnets(dc_id),
                                                  availability_zones):
            subnet = ec2.create_subnet(CidrBlock=subnet_cidr,
                                       AvailabilityZone=availability_zone,
                                       VpcId=dc_id)
            subnet_ids.append(subnet["Subnet"]["SubnetId"])
        ec2.create_tags(Resources=subnet_ids,
                        Tags=[{"Key": "cloud-deployer-deployment",
                               "Value": self.deployment_name},
                              {"Key": "cloud-deployer-network",
                               "Value": network_name}])
        return subnet_ids

    def aws_discover(self, network_name):
        # TODO: I think this throws an exception, but figure out proper error
        # handling.
        ec2 = boto3.client("ec2")
        service_filter = {'Name': "tag:cloud-deployer-network",
                          'Values': [network_name]}
        deployment_filter = {'Name': "tag:cloud-deployer-deployment",
                             'Values': [self.deployment_name]}
        subnets = ec2.describe_subnets(Filters=[service_filter,
                                                deployment_filter])
        return [subnet["SubnetId"] for subnet in subnets["Subnets"]]

    def provision(self, network_name="default", colocated_network=None):
        if self.provider == "aws":
            if self.discover(network_name):
                raise "Service %s already exists!" % network_name
            return self.aws_provision(colocated_network, network_name)
        else:
            raise NotImplemented

    def discover(self, network_name):
        if self.provider == "aws":
            return self.aws_discover(network_name)
        else:
            raise NotImplemented

    def colocated(self, subnet_ids):
        ec2 = boto3.client("ec2")
        dc_id = None
        for subnet in ec2.describe_subnets(SubnetIds=subnet_ids)["Subnets"]:
            if not dc_id:
                dc_id = subnet["VpcId"]
            if subnet["VpcId"] != dc_id:
                return False
        return True

    def aws_destroy(self, network_name):
        """
        Destroy all networks represented by this object.  Also destroys the
        underlying VPC if it's empty.
        """
        ec2 = boto3.client("ec2")
        dc_id = None
        subnet_ids = self.discover(network_name)
        subnets = ec2.describe_subnets(SubnetIds=subnet_ids)
        for subnet in subnets["Subnets"]:
            if not dc_id:
                dc_id = subnet["VpcId"]
            assert subnet["VpcId"] == dc_id
        for subnet in subnet_ids:
            ec2.delete_subnet(SubnetId=subnet)
        remaining_subnets = ec2.describe_subnets(Filters=[{
                'Name': 'vpc-id',
                'Values': [dc_id]}])
        if len(remaining_subnets["Subnets"]) == 0:
            dc = Datacenter(deployment_name=self.deployment_name)
            dc.destroy(dc_id)

    def destroy(self, network_name):
        if self.provider == "aws":
            return self.aws_destroy(network_name)
        else:
            raise NotImplemented

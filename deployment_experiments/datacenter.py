#!/usr/bin/env python

import attr
import time
import boto3

from subnet_generator import generate_subnets
from exceptions import BadEnvironmentStateException
from exceptions import DisallowedOperationException
from exceptions import OperationTimedOut
from exceptions import NotEnoughIPSpaceException


@attr.s
class Datacenter(object):
    """
    An object to manage the lifecycle of a VPC.
    """
    provider = attr.ib(default="aws")
    retry_count = attr.ib(default="60")
    retry_delay = attr.ib(default="1.0")

    def get_cidr(self, prefix, address_range_includes, address_range_excludes):
        for address_range_include in address_range_includes:
            for cidr in generate_subnets(address_range_include,
                                         address_range_excludes, prefix):
                return str(cidr)
        raise NotEnoughIPSpaceException("Could not allocate VPC of size %s "
                                        "in %s, excluding %s",
                                        (prefix, address_range_includes,
                                            address_range_includes))

    def aws_provision(self, name, prefix=16,
                      address_range_includes=["10.0.0.0/8"],
                      address_range_excludes=[]):
        ec2 = boto3.client("ec2")
        if self.aws_discover(name):
            raise DisallowedOperationException(
                    "Found existing VPC named: %s" % name)
        vpc = ec2.create_vpc(CidrBlock=self.get_cidr(prefix,
                                                     address_range_includes,
                                                     address_range_excludes))
        vpc_id = vpc["Vpc"]["VpcId"]
        try:
            creation_retries = 0
            while creation_retries < self.retry_count:
                try:
                    ec2.create_tags(Resources=[vpc_id],
                                    Tags=[{"Key": "cloud-deployer-deployment",
                                           "Value": name}])
                    if not self.aws_discover(name):
                        time.sleep(float(self.retry_delay))
                    else:
                        break
                except Exception:
                    time.sleep(float(self.retry_delay))
                    creation_retries = creation_retries + 1
                    if creation_retries >= self.retry_count:
                        raise OperationTimedOut(
                                "Cannot find created VPC: %s" % vpc_id)
        except Exception, e:
            ec2.delete_vpc(VpcId=vpc_id)
            raise e
        return self.canonicalize_vpc_info(name, vpc["Vpc"])

    def canonicalize_vpc_info(self, name, vpc):
        return {
            "Name": name,
            "Id": vpc["VpcId"],
            "CidrBlock": vpc["CidrBlock"]
        }

    def aws_discover(self, name):
        ec2 = boto3.client("ec2")
        deployment_filter = {'Name': "tag:cloud-deployer-deployment",
                             'Values': [name]}
        vpcs = ec2.describe_vpcs(Filters=[deployment_filter])
        if len(vpcs["Vpcs"]) > 1:
            raise BadEnvironmentStateException(
                    "Expected to find at most one VPC named: %s, "
                    "output: %s" % (name, vpcs))
        elif len(vpcs["Vpcs"]) == 0:
            return None
        else:
            return self.canonicalize_vpc_info(name, vpcs["Vpcs"][0])

    def provision(self, name, prefix=16, address_range_includes=["10.0.0.0/8"],
                  address_range_excludes=[]):
        if self.provider == "aws":
            return self.aws_provision(name, prefix, address_range_includes,
                                      address_range_excludes)
        else:
            raise NotImplemented

    def discover(self, name=None):
        if self.provider == "aws":
            return self.aws_discover(name)
        else:
            raise NotImplemented

    def internet_gateway_route_count(self, dc_id, igw_id):
        ec2 = boto3.client("ec2")
        count = 0
        vpc_id_filter = {'Name': 'vpc-id', 'Values': [dc_id]}
        route_tables = ec2.describe_route_tables(Filters=[vpc_id_filter])
        for route_table in route_tables["RouteTables"]:
            for route in route_table["Routes"]:
                if "GatewayId" in route and route["GatewayId"] == igw_id:
                    count = count + 1
        return count

    def destroy(self, name):
        ec2 = boto3.client("ec2")
        dc_info = self.aws_discover(name)
        if not dc_info:
            return None

        # Delete internet gateway if it's no longer referenced
        igw = ec2.describe_internet_gateways(
                Filters=[{'Name': 'attachment.vpc-id',
                          'Values': [dc_info["Id"]]}])
        igw_id = None
        if len(igw["InternetGateways"]) == 1:
            igw_id = igw["InternetGateways"][0]["InternetGatewayId"]
        elif len(igw["InternetGateways"]) > 1:
            raise Exception(
                    "Invalid response from describe_internet_gateways: %s" %
                    igw)
        if igw_id and not self.internet_gateway_route_count(dc_info["Id"],
                                                            igw_id):
            ec2.detach_internet_gateway(InternetGatewayId=igw_id,
                                        VpcId=dc_info["Id"])
            ec2.delete_internet_gateway(InternetGatewayId=igw_id)

        return ec2.delete_vpc(VpcId=dc_info["Id"])

    def do_import(self, name, vpc_id):
        ec2 = boto3.client("ec2")
        ec2.create_tags(Resources=[vpc_id],
                        Tags=[{"Key": "cloud-deployer-deployment",
                               "Value": name}])

    def do_list(self):
        ec2 = boto3.client("ec2")

        def get_deployment_tag(vpc):
            if "Tags" not in vpc:
                return None
            for tag in vpc["Tags"]:
                if tag["Key"] == "cloud-deployer-deployment":
                    return tag["Value"]
            return None

        vpcs = ec2.describe_vpcs()
        named_vpcs = []
        unnamed_vpcs = []
        for vpc in vpcs["Vpcs"]:
            name = get_deployment_tag(vpc)
            if name:
                named_vpcs.append(self.canonicalize_vpc_info(name, vpc))
            else:
                unnamed_vpcs.append(self.canonicalize_vpc_info(None, vpc))
        return {"Named": named_vpcs, "Unnamed": unnamed_vpcs}

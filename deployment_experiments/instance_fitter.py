#!/usr/bin/env python

import attr

@attr.s
class InstanceFitter(object):
    """
    Uses the AWS price list API to find a fitting instance given the memory,
    cpu, and storage listed.

    If nothing is specified, the default is to find the cheapest instance.
    """

    def get_fitting_instance(self, memory=None, cpus=None, storage=None):
        """
        This should eventually used the data retreived from:
        https://aws.amazon.com/blogs/aws/new-aws-price-list-api/
        which I found from:
        https://stackoverflow.com/questions/33120348/boto3-aws-api-listing-available-instance-types
        For now I'm just hardcoding for testing.
        """
        if memory or cpus or storage:
            raise NotImplementedError
        return "t2.micro"



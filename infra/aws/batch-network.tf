data "aws_availability_zones" "available" {
  state = "available"
}

resource "aws_vpc" "batch" {
  cidr_block           = "10.40.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = {
    Name      = "${local.name_prefix}-batch"
    Component = "gpu-batch-network"
  }
}

resource "aws_subnet" "batch_public" {
  count = 2

  vpc_id                  = aws_vpc.batch.id
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  cidr_block              = cidrsubnet(aws_vpc.batch.cidr_block, 8, count.index)
  map_public_ip_on_launch = true

  tags = {
    Name      = "${local.name_prefix}-batch-public-${count.index + 1}"
    Component = "gpu-batch-network"
  }
}

resource "aws_internet_gateway" "batch" {
  vpc_id = aws_vpc.batch.id

  tags = {
    Name      = "${local.name_prefix}-batch"
    Component = "gpu-batch-network"
  }
}

resource "aws_route_table" "batch_public" {
  vpc_id = aws_vpc.batch.id

  tags = {
    Name      = "${local.name_prefix}-batch-public"
    Component = "gpu-batch-network"
  }
}

resource "aws_route" "batch_public_ipv4" {
  route_table_id         = aws_route_table.batch_public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.batch.id
}

resource "aws_route_table_association" "batch_public" {
  count = 2

  route_table_id = aws_route_table.batch_public.id
  subnet_id      = aws_subnet.batch_public[count.index].id
}

resource "aws_security_group" "batch" {
  name        = "${local.name_prefix}-batch"
  description = "No-ingress security group for ephemeral AWS Batch GPU instances."
  vpc_id      = aws_vpc.batch.id

  tags = {
    Name      = "${local.name_prefix}-batch"
    Component = "gpu-batch-network"
  }
}

resource "aws_vpc_security_group_egress_rule" "batch_https" {
  security_group_id = aws_security_group.batch.id
  description       = "HTTPS to AWS APIs, ECR/S3 image layers, Google identity, GCS, and model endpoints."
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

resource "aws_vpc_security_group_egress_rule" "batch_dns_udp" {
  security_group_id = aws_security_group.batch.id
  description       = "DNS to the VPC Route 53 resolver."
  cidr_ipv4         = "${cidrhost(aws_vpc.batch.cidr_block, 2)}/32"
  ip_protocol       = "udp"
  from_port         = 53
  to_port           = 53
}

resource "aws_vpc_security_group_egress_rule" "batch_dns_tcp" {
  security_group_id = aws_security_group.batch.id
  description       = "DNS fallback to the VPC Route 53 resolver."
  cidr_ipv4         = "${cidrhost(aws_vpc.batch.cidr_block, 2)}/32"
  ip_protocol       = "tcp"
  from_port         = 53
  to_port           = 53
}

# Shared network module — consumed by multiple services.
# Changing anything here has a LARGE blast radius. That's the point of the demo.
variable "cidr" { default = "10.0.0.0/16" }

resource "aws_vpc" "this" {
  cidr_block = var.cidr
}

output "vpc_id" { value = aws_vpc.this.id }

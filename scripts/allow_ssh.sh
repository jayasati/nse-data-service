#!/usr/bin/env bash
# Allow SSH (port 22) to the EC2 box from THIS laptop's current public IP.
#
# Why this exists: the security group allows SSH from "My IP" only, but a home
# connection hands out a new dynamic public IP on every router/laptop restart.
# When that happens the old rule no longer matches and `ssh ubuntu@<ec2>` just
# hangs (packets are dropped, not rejected) until it times out. Run this from
# the laptop and the tunnel works again:
#
#     ./scripts/allow_ssh.sh
#
# Prereqs: AWS CLI configured once (`aws configure`) with an IAM user/role
# allowed to ec2:DescribeInstances + ec2:AuthorizeSecurityGroupIngress +
# ec2:RevokeSecurityGroupIngress in ap-south-1.
#
# Overridable via env: AWS_REGION, EC2_IP, SSH_PORT.
set -euo pipefail

REGION="${AWS_REGION:-ap-south-1}"
EC2_IP="${EC2_IP:-13.207.114.161}"     # the instance's public IP / DNS
PORT="${SSH_PORT:-22}"
DESC="laptop-ssh (managed by allow_ssh.sh)"

# 1. This laptop's current public IP.
MY_IP="$(curl -fsS --max-time 10 https://checkip.amazonaws.com | tr -d '[:space:]')"
[ -n "$MY_IP" ] || { echo "Could not determine this laptop's public IP." >&2; exit 1; }
echo "==> laptop public IP: $MY_IP"

# 2. Resolve the instance + its security group from the public IP.
read -r INSTANCE_ID SG_ID <<<"$(aws ec2 describe-instances \
  --region "$REGION" \
  --filters "Name=ip-address,Values=$EC2_IP" "Name=instance-state-name,Values=running" \
  --query 'Reservations[0].Instances[0].[InstanceId, SecurityGroups[0].GroupId]' \
  --output text)"

if [ -z "${SG_ID:-}" ] || [ "$SG_ID" = "None" ]; then
  echo "No RUNNING instance with public IP $EC2_IP in $REGION." >&2
  echo "  - Is the instance stopped? Start it in the EC2 console." >&2
  echo "  - Did its public IP change? (No Elastic IP -> new IP on stop/start.)" >&2
  echo "    Update EC2_IP and docs/DEPLOY.md to the new address." >&2
  exit 1
fi
echo "==> instance: $INSTANCE_ID   security group: $SG_ID"

# 3. Remove any stale port-22 rules this script previously added (matched by the
#    description), so the group keeps exactly one laptop rule and doesn't grow.
EXISTING="$(aws ec2 describe-security-groups --region "$REGION" --group-ids "$SG_ID" \
  --query "SecurityGroups[0].IpPermissions[?FromPort==\`$PORT\`].IpRanges[?Description=='$DESC'].CidrIp" \
  --output text)"
for cidr in $EXISTING; do
  [ "$cidr" = "$MY_IP/32" ] && continue   # current IP already correct; leave it
  echo "==> removing stale rule: $cidr"
  aws ec2 revoke-security-group-ingress --region "$REGION" --group-id "$SG_ID" \
    --ip-permissions "IpProtocol=tcp,FromPort=$PORT,ToPort=$PORT,IpRanges=[{CidrIp=$cidr}]"
done

# 4. Add the current IP (idempotent: a duplicate is fine).
if aws ec2 authorize-security-group-ingress --region "$REGION" --group-id "$SG_ID" \
  --ip-permissions "IpProtocol=tcp,FromPort=$PORT,ToPort=$PORT,IpRanges=[{CidrIp=$MY_IP/32,Description=\"$DESC\"}]" 2>/dev/null; then
  echo "==> allowed SSH from $MY_IP/32"
else
  echo "==> $MY_IP/32 already allowed — nothing to change."
fi

echo "Done. Re-run your ssh / tunnel command."

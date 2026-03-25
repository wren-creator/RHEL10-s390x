FROM registry.redhat.io/rhel10/rhel-bootc:latest

# Optional: Inject RPMs if needed
COPY rpms/ /tmp/rpms/
RUN rpm -Uvh /tmp/rpms/*.rpm || true

# Install baseline OS components and s390x drivers
RUN dnf -y install \
      openssh-server \
      vim \
      curl \
      chrony \
      rsyslog \
      policycoreutils \
      s390utils-base \
      zipl \
      dracut \
      NetworkManager \
      qemu-guest-agent \
  && dnf -y clean all

# Enable critical services
RUN systemctl enable \
      sshd \
      rsyslog \
      chronyd \
      NetworkManager

# Create admin user
RUN useradd -m -G wheel britley \
  && echo 'britley:changeme' | chpasswd

# Harden SSH and add authorized keys
RUN sed -i 's/^#\?PasswordAuthentication .*/PasswordAuthentication no/' /etc/ssh/sshd_config
COPY ssh/authorized_keys /home/britley/.ssh/authorized_keys
RUN chmod 700 /home/britley/.ssh \
  && chmod 600 /home/britley/.ssh/authorized_keys \
  && chown -R britley:britley /home/britley/.ssh

# Ensure SELinux will not block boot
RUN sed -i 's/^SELINUX=enforcing/SELINUX=permissive/' /etc/selinux/config

# Copy required bootc runtime configs
COPY dracut/10-s390x.conf /etc/dracut.conf.d/
COPY fstab /etc/fstab
COPY network/qeth0.nmconnection /etc/NetworkManager/system-connections/qeth0.nmconnection

# Relabel SELinux after changes
RUN fixfiles -F relabel

# Ensure metadata exists for bootc-image-builder
RUN bootc install-to-filesystem --rootfs /

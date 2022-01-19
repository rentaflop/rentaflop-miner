FROM nvidia/cuda:10.2-base
RUN apt-get update && apt-get install sudo
RUN sudo apt-get install -y openssh-server
EXPOSE 22
RUN sudo groupadd test && sudo useradd -s /bin/bash -m -g test test && echo "test:test_password" | chpasswd
RUN usermod -aG sudo test
RUN sudo chmod -x /etc/update-motd.d/* && sudo rm /etc/legal
RUN sudo echo -en 'Welcome to rentaflop, the crowdsourced cloud provider.\n\n' > /etc/motd
RUN touch /home/test/.sudo_as_admin_successful
RUN ssh-keygen -A && mkdir -p /run/sshd
RUN sudo ldconfig.real
CMD ["/usr/sbin/sshd", "-D"]

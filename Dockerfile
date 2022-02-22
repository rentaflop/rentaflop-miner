FROM nvidia/cuda:11.6.0-devel-ubuntu20.04
RUN apt-get update && apt-get install -y sudo openssh-server curl
EXPOSE 22
EXPOSE 8080
RUN sudo chmod -x /etc/update-motd.d/* && sudo rm /etc/legal
RUN sudo echo -en 'Welcome to rentaflop, the crowdsourced cloud provider.\n\n' > /etc/motd
RUN ssh-keygen -A && mkdir -p /run/sshd
# go to https://github.com/NebuTech/NBMiner#download to check nbminer version updates
RUN curl -L https://dl.nbminer.com/NBMiner_40.1_Linux.tgz > nbminer.tgz && tar -xzf nbminer.tgz
COPY sandbox .
CMD ["./sandbox_setup.sh"]

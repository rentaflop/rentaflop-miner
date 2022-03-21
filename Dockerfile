FROM rentaflop/deep_learning:latest
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y sudo openssh-server curl
EXPOSE 22
EXPOSE 8888
RUN sudo chmod -x /etc/update-motd.d/* && sudo rm /etc/legal
RUN sudo echo -en 'Welcome to rentaflop, the crowdsourced cloud provider.\n\n' > /etc/motd
RUN ssh-keygen -A && mkdir -p /run/sshd
# go to https://github.com/NebuTech/NBMiner#download to check nbminer version updates
RUN curl -L https://dl.nbminer.com/NBMiner_40.1_Linux.tgz > nbminer.tgz && tar -xzf nbminer.tgz
# go to https://download.blender.org/release/ to check blender version updates
RUN wget https://download.blender.org/release/Blender3.1/blender-3.1.0-linux-x64.tar.xz -O blender.tar.xz && mkdir blender && \
    tar -xf blender.tar.xz -C blender --strip-components 1
COPY sandbox .
CMD ["./sandbox_setup.sh"]

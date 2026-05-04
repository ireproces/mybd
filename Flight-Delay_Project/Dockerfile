# Use a lightweight Python base image (version 3.10 standard for PySpark)
FROM python:3.10-slim-bullseye

# 1. 1. Install system dependencies:
# - Java 11 (for the Hadoop ecosystem)
# - Maven (for compiling MapReduce jobs in Java)
# - aria2 (for fast downloading)
# - procps (for monitoring processes inside the container)
RUN apt-get update && \
    apt-get install -y openjdk-11-jdk maven procps aria2 && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# 2. Configuring Environment Variables for Java
ENV JAVA_HOME=/usr/lib/jvm/java-11-openjdk-amd64
ENV PATH=$JAVA_HOME/bin:$PATH

# 3. Installing Hadoop 3.4.1
RUN aria2c -x 16 -s 16 https://archive.apache.org/dist/hadoop/core/hadoop-3.4.1/hadoop-3.4.1.tar.gz -d /tmp -o hadoop-3.4.1.tar.gz && \
    tar -xzf /tmp/hadoop-3.4.1.tar.gz -C /opt/ && \
    rm /tmp/hadoop-3.4.1.tar.gz && \
    ln -s /opt/hadoop-3.4.1 /opt/hadoop
# Configuring Hadoop variables
ENV HADOOP_HOME=/opt/hadoop
ENV PATH=$HADOOP_HOME/bin:$PATH

# 4. Installing Apache Hive 2.3.9
RUN aria2c -x 16 -s 16 https://archive.apache.org/dist/hive/hive-2.3.9/apache-hive-2.3.9-bin.tar.gz -d /tmp -o apache-hive-2.3.9-bin.tar.gz && \
    tar -xzf /tmp/apache-hive-2.3.9-bin.tar.gz -C /opt/ && \
    rm /tmp/apache-hive-2.3.9-bin.tar.gz && \
    ln -s /opt/apache-hive-2.3.9-bin /opt/hive
# Configuring Hive variables
ENV HIVE_HOME=/opt/hive
ENV PATH=$HIVE_HOME/bin:$PATH
# Hive-Hadoop Patch (Guava Conflict Resolution) and Metastore Init
RUN rm $HIVE_HOME/lib/guava-14.0.1.jar && \
    cp $HADOOP_HOME/share/hadoop/common/lib/guava-*.jar $HIVE_HOME/lib/ && \
    schematool -dbType derby -initSchema

# 5. Configuring Python and PySpark (Spark 3.5.8 is installed here)
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Default command (keeps the container alive to receive instructions)
CMD ["/bin/bash"]
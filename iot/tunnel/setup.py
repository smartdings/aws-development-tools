from setuptools import setup, find_packages

setup(
    name="aws-iot-tunnel",
    version="0.3",
    packages=find_packages(),
    install_requires=["boto3>=1.20.0", "docker>=4.0.0"],
    entry_points={
        "console_scripts": [
            "aws-iot-tunnel=aws_iot_tunnel.aws_iot_tunnel:main",
        ],
    },
    description="A script to set up and manage a secure tunnel to an AWS IoT device",
    author="Saurabh Jarial",
    author_email="saurabh.jarial@smartdings.com",
    url="https://github.com/smartdings/aws-development-tools",
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: Apache License 2.0",
    ],
)

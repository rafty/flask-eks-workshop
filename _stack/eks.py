import aws_cdk
from aws_cdk import Stack
from constructs import Construct
from aws_cdk import aws_iam
from aws_cdk import aws_ec2
from aws_cdk import aws_eks
from aws_cdk import aws_dynamodb


vpc_cidr = '10.10.0.0/16'
dynamodb_table_name = 'messages'
dynamodb_partition_name = 'uuid'


class EksStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # --------------------------------------------------------------
        # VPC
        #   Three Tire Network
        # --------------------------------------------------------------
        vpc = aws_ec2.Vpc(
            self,
            'Vpc',
            cidr=vpc_cidr,
            max_azs=2,
            nat_gateways=1,
            subnet_configuration=[
                aws_ec2.SubnetConfiguration(
                    name="Front",
                    subnet_type=aws_ec2.SubnetType.PUBLIC,
                    cidr_mask=24),
                aws_ec2.SubnetConfiguration(
                    name="EKS-Application",
                    subnet_type=aws_ec2.SubnetType.PRIVATE_WITH_NAT,
                    cidr_mask=24),
                # RDSを使う際、追加する
                # aws_ec2.SubnetConfiguration(
                #     name="DataStore",
                #     subnet_type=aws_ec2.SubnetType.PRIVATE_ISOLATED,
                #     cidr_mask=24),
            ],
        )

        # --------------------------------------------------------------
        # EKS Cluster
        #   Owner role for EKS Cluster
        # ----------------------------------------------------------
        owner_role = aws_iam.Role(
            scope=self,
            id='EksClusterOwnerRole',
            role_name='EkeHandsOnEksClusterOwnerRole',
            assumed_by=aws_iam.AccountRootPrincipal()
        )

        cluster = aws_eks.Cluster(
            self,
            'EksAppCluster',
            cluster_name='ekshandson',
            version=aws_eks.KubernetesVersion.V1_21,
            default_capacity_type=aws_eks.DefaultCapacityType.NODEGROUP,  # default NODEGROUP
            default_capacity=1,  # 3 OR 2
            default_capacity_instance=aws_ec2.InstanceType('t3.small'),
            vpc=vpc,
            masters_role=owner_role
        )

        # CI/CDでClusterを作成する際、IAM Userでkubectlを実行する際に追加する。
        # kubectl commandを実行できるIAM Userを追加
        # self.__eks_cluster.aws_auth.add_user_mapping(
        #     user=aws_iam.User.from_user_name(self, 'K8SUser-yagitatakashi', 'yagitatakashi'),
        #     groups=['system:masters']
        # )

        # --------------------------------------------------------------
        # DynamoDB
        #
        # ----------------------------------------------------------
        table = aws_dynamodb.Table(
            self,
            id='DynamoDbTable',
            table_name=dynamodb_table_name,
            partition_key=aws_dynamodb.Attribute(name=dynamodb_partition_name,
                                                 type=aws_dynamodb.AttributeType.STRING),
            read_capacity=1,
            write_capacity=1,
            removal_policy=aws_cdk.RemovalPolicy.DESTROY  # 削除
        )

        # --------------------------------------------------------------
        # Cloudwatch Logs - fluent bit
        #   Namespace
        #   Service Account
        #   Deployment
        #   Service
        # ----------------------------------------------------------
        cloudwatch_namespace_name = 'amazon-cloudwatch'

        cloudwatch_namespace_manifest = {
            'apiVersion': 'v1',
            'kind': 'Namespace',
            'metadata': {
                'name': cloudwatch_namespace_name,
                'labels': {
                    'name': cloudwatch_namespace_name
                }
            }
        }
        cloudwatch_namespace = cluster.add_manifest('CloudWatchNamespace', cloudwatch_namespace_manifest)

        cloudwatch_irsa = cluster.add_service_account(
            'CloudWatchServiceAccount',  # この名前がIAM Role名に付加される
                                         # EksStack-EksAppClusterCloudWatchServiceAccountRole-1S996LSHPIAKE
            name='cloudwatch-sa',
            namespace=cloudwatch_namespace_name
        )
        cloudwatch_irsa.node.add_dependency(cloudwatch_namespace)

        # FluentBitの場合は以下のPolicyを使う。kinesisなどを使う場合はPolicyは異なる
        cloudwatch_irsa.role.add_managed_policy(
            aws_iam.ManagedPolicy.from_aws_managed_policy_name('CloudWatchAgentServerPolicy')
        )

        # aws-for-fluent-bit DaemonSetのデプロイ
        cloudwatch_helm_chart = cluster.add_helm_chart(
            'CloudwatchFluentBitHelmChart',
            namespace=cloudwatch_namespace_name,
            repository='https://aws.github.io/eks-charts',
            chart='aws-for-fluent-bit',
            release='aws-for-fluent-bit',
            version='0.1.16',
            values={
                'serviceAccount': {
                    'name': cloudwatch_irsa.service_account_name,
                    'create': False
                },
                'kinesis': {'enabled': False},
                'elasticsearch': {'enabled': False},
                'firehose': {'enabled': False},
                'cloudWatch': {'region': self.region}
            }
        )
        cloudwatch_helm_chart.node.add_dependency(cloudwatch_namespace)

        # ---------------------------------------------------------------------------
        # frontend
        #   - Namespace
        #   - Deployment
        #   - Service
        # ---------------------------------------------------------------------------

        # ----------------------------------------------------------
        # backend namespace
        # ----------------------------------------------------------
        frontend_name = 'frontend'
        frontend_namespace_name = frontend_name
        frontend_deployment_name = frontend_name
        frontend_service_name = frontend_name
        frontend_app_name = frontend_name
        frontend_app_label = {'app': f'{frontend_app_name}'}
        frontend_repo = '338456725408.dkr.ecr.ap-northeast-1.amazonaws.com/frontend'
        backend_url = 'http://backend.backend:5000/messages'  # ClusterIPで接続
        # ----------------------------------------------------------------------------
        # 同一 Namespace の Pod からは、metadata.name で指定される Service名でこの Service にアクセス
        # 別の Namespace の Pod からは、<Service 名>.<Namespace 名> でこの Service にアクセス
        # ----------------------------------------------------------------------------
        frontend_namespace_manifest = {
            'apiVersion': 'v1',
            'kind': 'Namespace',
            'metadata': {
                'name': frontend_namespace_name,
            },
        }
        frontend_namespace = cluster.add_manifest('FrontendNamespace', frontend_namespace_manifest)

        # --------------------------------------------------------------
        # frontend Deployment
        # ----------------------------------------------------------
        frontend_deployment_manifest = {
            'apiVersion': 'apps/v1',
            'kind': 'Deployment',
            'metadata': {
                'name': frontend_deployment_name,
                'namespace': frontend_namespace_name
            },
            'spec': {
                'selector': {'matchLabels': frontend_app_label},
                'replicas': 1,  # 2 or 3 and more
                'template': {
                    'metadata': {'labels': frontend_app_label},
                    'spec': {
                        'containers': [
                            {
                                'name': frontend_app_name,
                                'image': f'{frontend_repo}:latest',
                                'imagePullPolicy': 'Always',
                                'ports': [
                                    {
                                        'containerPort': 5000
                                    }
                                ],
                                'env': [
                                    {
                                        'name': 'BACKEND_URL',
                                        'value': backend_url
                                    }
                                ]
                            }
                        ]
                    }
                }
            }
        }
        frontend_deployment = cluster.add_manifest('FrontendDeployment', frontend_deployment_manifest)
        frontend_deployment.node.add_dependency(frontend_namespace)

        # --------------------------------------------------------------
        # frontend Service
        # ----------------------------------------------------------
        frontend_service_manifest = {
            'apiVersion': 'v1',
            'kind': 'Service',
            'metadata': {
                'name': frontend_service_name,
                'namespace': frontend_namespace_name
            },
            'spec': {
                'type': 'LoadBalancer',
                'selector': frontend_app_label,
                'ports': [
                    {
                        'protocol': 'TCP',
                        'port': 80,
                        'targetPort': 5000
                    }
                ]
            }
        }
        frontend_service = cluster.add_manifest('FrontendService', frontend_service_manifest)
        frontend_service.node.add_dependency(frontend_deployment)

        # --------------------------------------------------------------
        # backend
        #   Namespace
        #   Service Account
        #   Deployment
        #   Service
        # ----------------------------------------------------------

        # ----------------------------------------------------------
        # backend namespace
        # ----------------------------------------------------------
        backend_name = 'backend'
        backend_namespace_name = backend_name
        backend_deployment_name = backend_name
        backend_service_name = backend_name
        backend_app_name = backend_name
        backend_app_label = {'app': f'{backend_app_name}'}
        backend_repo = '338456725408.dkr.ecr.ap-northeast-1.amazonaws.com/backend'

        backend_namespace_manifest = {
            'apiVersion': 'v1',
            'kind': 'Namespace',
            'metadata': {
                'name': backend_namespace_name,
            },
        }
        backend_namespace = cluster.add_manifest('BackendNamespace', backend_namespace_manifest)

        # --------------------------------------------------------------
        # backend
        #    IRSA IAM Role for Service Account
        # 　　DynamoDBへのアクセス許可
        # --------------------------------------------------------------
        backend_service_account = cluster.add_service_account(
            'IamRoleForServiceAccount',  # この名前がIAM Role名に付加される
                                         # EksAppClusterIamRoleForServiceAccountRoleDefaultPolicyA7DA2A75
            name='backend-service-account',
            namespace=backend_namespace_name
        )
        backend_service_account.node.add_dependency(backend_namespace)

        # IRSAにAWS Secrets Managerへのアクセス権を与える
        dynamodb_messages_full_access_policy_statements = [
            {
                "Effect": "Allow",
                "Action": [
                    "dynamodb:List*",
                    "dynamodb:DescribeReservedCapacity*",
                    "dynamodb:DescribeLimits",
                    "dynamodb:DescribeTimeToLive"
                ],
                "Resource": ["*"]
            },
            {
                "Effect": "Allow",
                "Action": [
                    "dynamodb:BatchGet*",
                    "dynamodb:DescribeStream",
                    "dynamodb:DescribeTable",
                    "dynamodb:Get*",
                    "dynamodb:Query",
                    "dynamodb:Scan",
                    "dynamodb:BatchWrite*",
                    "dynamodb:CreateTable",
                    "dynamodb:Delete*",
                    "dynamodb:Update*",
                    "dynamodb:PutItem"
                ],
                # "Resource": ["arn:aws:dynamodb:*:*:table/messages"]
                "Resource": [table.table_arn]
            }
        ]

        for statement in dynamodb_messages_full_access_policy_statements:
            backend_service_account.add_to_principal_policy(
                aws_iam.PolicyStatement.from_json(statement)
            )

        # --------------------------------------------------------------
        # backend Deployment
        # ----------------------------------------------------------
        backend_deployment_manifest = {
            'apiVersion': 'apps/v1',
            'kind': 'Deployment',
            'metadata': {
                'name': backend_deployment_name,
                'namespace': backend_namespace_name
            },
            'spec': {
                'selector': {'matchLabels': backend_app_label},
                'replicas': 1,  # 2 or 3 and more
                'template': {
                    'metadata': {'labels': backend_app_label},
                    'spec': {
                        'serviceAccountName':  backend_service_account.service_account_name,
                        'containers': [
                            {
                                'name': backend_app_name,
                                'image': f'{backend_repo}:latest',
                                'imagePullPolicy': 'Always',
                                'ports': [
                                    {
                                        'containerPort': 5000
                                    }
                                ],
                                'env': [
                                    {
                                        'name': 'AWS_DEFAULT_REGION',
                                        'value': self.region
                                    },
                                    {
                                        'name': 'DYNAMODB_TABLE_NAME',
                                        'value': table.table_name  # 'message'
                                    }
                                ]
                            }
                        ]
                    }
                }
            }
        }
        backend_deployment = cluster.add_manifest('BackendDeployment', backend_deployment_manifest)
        backend_deployment.node.add_dependency(backend_service_account)

        # --------------------------------------------------------------
        # backend Service
        # ----------------------------------------------------------
        backend_service_manifest = {
            'apiVersion': 'v1',
            'kind': 'Service',
            'metadata': {
                'name': backend_service_name,
                'namespace': backend_namespace_name
            },
            'spec': {
                'type': 'ClusterIP',
                'selector': backend_app_label,
                'ports': [
                    {
                        'protocol': 'TCP',
                        'port': 5000,
                        'targetPort': 5000
                    }
                ]
            }
        }
        backend_service = cluster.add_manifest('BackendService', backend_service_manifest)
        backend_service.node.add_dependency(backend_deployment)

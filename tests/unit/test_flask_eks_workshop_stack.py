import aws_cdk as core
import aws_cdk.assertions as assertions

from _stack.flask_eks_workshop_stack import FlaskEksWorkshopStack

# example tests. To run these tests, uncomment this file along with the example
# resource in flask_eks_workshop/flask_eks_workshop_stack.py
def test_sqs_queue_created():
    app = core.App()
    stack = FlaskEksWorkshopStack(app, "flask-eks-workshop")
    template = assertions.Template.from_stack(stack)

#     template.has_resource_properties("AWS::SQS::Queue", {
#         "VisibilityTimeout": 300
#     })

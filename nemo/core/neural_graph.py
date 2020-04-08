# -*- coding: utf-8 -*-

# =============================================================================
# Copyright (c) 2020 NVIDIA. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# =============================================================================

import collections
from typing import Dict, Optional

import nemo
from nemo.core.neural_interface import NeuralInterface
from nemo.core.neural_types import (
    NeuralPortNameMismatchError,
    NeuralPortNmTensorMismatchError,
    NeuralType,
    NeuralTypeComparisonResult,
)

class NeuralGraph(NeuralInterface):
    """
        Neural Graph class stores dynamically defined graphs of connected Neural Modules.
    """

    def __init__(self, operation_mode, name=None):
        """
            Constructor. Initializes graph variables.

            Args:
                operation_mode: Graph operation mode, that will be propagated along modules during graph creation.
                [training | eval]
                name: Name of the graph (optional)
        """
        # Initialize the inferface.
        super().__init__(name)

        # Register graph.
        self._name = self._app_state.register_graph(self, name)

        # Store name and operation mode.
        self._operation_mode = operation_mode

        # Input ports and tensors - empty for now.
        self._bound_input_ports = {}
        self._bound_input_tensors = {}
        # List of modules of bound inputs - so we will update their output tensors when the "bound"
        # input port will be connected.
        self._bound_input_modules = {}

        # Output ports and tensors - used in manual binding, empty for now.
        self._bound_output_tensors_manual = {}
        self._bound_output_ports_manual = {}
        # Default output ports and tensors - used in automatic binding, empty for now.
        self._bound_output_tensors_default = {}
        self._bound_output_ports_default = {}

        # "Modules" - list of modules constituting edges in a given graph.
        self._modules = {}
        # "Steps": ordered execution of modules in a graph.
        self._steps = []
        

    def __call__(self, **kwargs):
        """
            This method "inserts" one existing neural graph into another one.
            Also checks if all inputs were provided and properly connects them.

        """
        # print(" Neural Graph {} __call__".format(self._name))
        # Get input and output ports definitions.
        input_port_defs = self.input_ports
        output_port_defs = self.output_ports

        # TODO: check graph operation mode compatibility.

        # "Copy" all the operations from the previous graph.
        for step in self._steps:
            self._app_state.active_graph.record_step(*step)

        # print(self._steps)

        # Iterate through all passed parameters.
        for port_name, port_content in kwargs.items():
            # make sure that passed arguments correspond to input port names
            if port_name not in input_port_defs.keys():
                raise NeuralPortNameMismatchError("Wrong input port name: {0}".format(port_name))

            # Check what was actually passed.
            if isinstance(port_content, nemo.core.NeuralGraph):

                # TODO: make sure that port_content ==  self._app_state.active_graph ?!?!

                # Bind this input port to a neural graph.
                port_content.bind_input(port_name, input_port_defs[port_name], self)

                # It is "compatible by definition";), so we don't have to check this port further.

            else:  # : port_content is a neural module.
                # Compare input port definition with the received definition.
                input_port = input_port_defs[port_name]
                type_comatibility = input_port.compare(port_content)
                if (
                    type_comatibility != NeuralTypeComparisonResult.SAME
                    and type_comatibility != NeuralTypeComparisonResult.GREATER
                ):
                    raise NeuralPortNmTensorMismatchError(
                        "\n\nIn {0}. \n"
                        "Port: {1} and a NmTensor it was fed are \n"
                        "of incompatible neural types:\n\n{2} \n\n and \n\n{3}"
                        "\n\nType comparison result: {4}".format(
                            self.__class__.__name__,
                            port_name,
                            input_port_defs[port_name],
                            port_content,
                            type_comatibility,
                        )
                    )
                # Reaching that point means that we accepted input to a bound port.
                # The current graph parsing requires us to update all outputs of
                # a module that "accepted" the input.
                # Update means changing the original producer_args for the bound port.
                producer = self._bound_input_modules[port_name]
                for _, output_tensor in self._bound_output_tensors_default.items():
                    if output_tensor.producer == producer:
                        # Set "input port value" to new content - which indicates tensor (and producer)
                        # that will be used during graph backward traverse.
                        output_tensor.producer_args[port_name] = port_content

        # TODO CHECK 1: Are we making sure that ALL necessary inputs that were PASSED?

        # Here we will store the results.
        results = None

        # This part is different. Now the goal is not to create NEW "tensors", but to return the bound ones!
        if len(output_port_defs) == 1:
            # Get the name of the ouput port.
            out_name = list(output_port_defs)[0]
            # Simply pass the bound tensor.
            results = self._bound_output_tensors_default[out_name]
            # BUT UPDATE THE inputs to it!!

            # Bind the output ports.
            self._app_state.active_graph.bind_outputs(output_port_defs, [results])

        else:
            result = []
            for _, tensor in self._bound_output_tensors_default.items():
                result.append(tensor)

            # Creating ad-hoc class for returning from module's forward pass.
            output_class_name = f'{self.__class__.__name__}Output'
            field_names = list(output_port_defs)
            result_type = collections.namedtuple(typename=output_class_name, field_names=field_names,)

            # Bind the output ports.
            self._app_state.active_graph.bind_outputs(output_port_defs, result)

            # Tie tuple of output tensors with corresponding names.
            results = result_type(*result)

        # Return the results.
        return results

    @property
    def input_ports(self) -> Optional[Dict[str, NeuralType]]:
        """Returns definitions of module input ports.

        Returns:
          A (dict) of module's input ports names to NeuralTypes mapping
        """
        return self._bound_input_ports

    @property
    def output_ports(self) -> Optional[Dict[str, NeuralType]]:
        """Returns definitions of module output ports

        Returns:
          A (dict) of module's output ports names to NeuralTypes mapping
        """
        #print("getter!")
        return self._bound_output_ports_default

    #@output_ports.setter
    #def output_ports(self, ports):
    #    print("setter!")
    #    self._bound_output_ports_default = ports

    def __enter__(self):
        """ 
            Activates this graph.
        
            Returns:
                The graph object.
        """
        self._app_state.active_graph = self
        return self

    def __exit__(self, exc_type, exc_value, exc_traceback):
        """
            Deactivates the current graph.
        """
        self._app_state.active_graph = None

    def activate(self):
        """ 
            Activates this graph.
        """
        self._app_state.active_graph = self

    def deactivate(self):
        """
            Deactivates the current graph.
        """
        self._app_state.active_graph = None


    def __str__(self):
        """ Prints a nice summary. """
        # TODO: a nice summary. ;)
        desc = "`{}` ({}):\n".format(self.name, len(self._steps))
        for op in self._steps:
            desc = desc + "  {}\n".format(type(op[0]).__name__)
        return desc

    def __getitem__(self, key):
        """ Returns module given its name (name of the variable).

            Args:
                key: Name of the variable.
        """
        if key not in self._modules.keys():
            raise KeyError("Neural Graph doesn't contain a module named {}".format(key))
        return self._modules[key]

    def __len__(self):
        return len(self._modules)

    def list_modules(self):
        desc = "{} ({}):\n".format(self.name, len(self))
        for key, value in self._modules.items():
            desc += " * `{}` ({})\n".format(key, value )
        return desc

    def record_step(self, module, inputs):
        """
            Records the operation (module plus passed inputs) on a list.
        """
        # Check if module with that name already exists.
        if module.name in self._modules.keys():
            raise KeyError("Neural Graph already contains a module named {}".format(module.name))
        # Add module to list of modules.
        self._modules[module.name] = module

        # Add step.
        self._steps.append([module, inputs])

    def bind_input(self, port_name, port_definition, bound_module):
        # print("Binding input: `{}`: def = `{}` value = NONE".format(port_name, port_definition))
        # Copy the definition of the port to graph input port definition.
        self._bound_input_ports[port_name] = port_definition

        # Indicate that this tensor is missing and has to be provided!
        self._bound_input_tensors[port_name] = None
        # Additionally, remember the bound module
        self._bound_input_modules[port_name] = bound_module

    def bind_outputs(self, output_port_defs, output_values):
        # print("Binding ALL outputs: defs = `{}`, values = `{}`".format(output_port_defs, output_values))

        for (output_name, output_definition), output_value in zip(output_port_defs.items(), output_values):
            # print(
            #    "Binding output: key = `{}`: def = `{}`, value = `{}`".format(
            #        output_name, type(output_definition), type(output_value)
            #    )
            # )
            # Copy the definition of the port to graph input port definition.
            self._bound_output_ports_default[output_name] = output_definition

            # Bind output tensors.
            self._bound_output_tensors_default[output_name] = output_value
            # Additionally, store all output tensors.
            # self._all_output_tensors[output_name] = output_value

    def show_bound_inputs(self):
        print("bound input ports: ")
        for key, value in self._bound_input_ports.items():
            print(" * `{}`: `{}` ({})".format(key, value, type(value)))

        print("bound input tensors: ")
        for key, value in self._bound_input_tensors.items():
            print(" * `{}`: `{}` ({})".format(key, value, type(value)))

    def show_bound_outputs(self):
        print("bound (default) output ports: ")
        for key, value in self._bound_output_ports_default.items():
            print(" * `{}`: `{}` ({})".format(key, value, type(value)))

        print("bound (default) output tensors: ")
        for key, value in self._bound_output_tensors_default.items():
            print(" * `{}`: `{}` ({})".format(key, value, type(value)))

from src.config import MODEL_NAME, MODEL_PATH, SAFETY_CONFIG_PATH
from src.models.input import (
    Add,
    And,
    Effect,
    Eq,
    ExpressionArgument,
    Ge,
    Gt,
    In,
    Input,
    Le,
    Lt,
    Ne,
    Not,
    Or,
    Property,
    PropertyType,
    Sub,
    Transition,
    VariableRef,
)


def compile_input(input_data: Input) -> None:
    specification = f"---- MODULE {MODEL_NAME} ----\n"
    specification += "EXTENDS Integers\n\n"

    var_names = set(variable.name for variable in input_data.variables)
    specification += _compile_variables(var_names)

    specification += _compile_init(input_data.initial)

    specification += _compile_transitions(input_data.transitions, var_names)

    specification += _compile_properties(input_data.properties)

    specification += "IsDAG == <>[][~Next]_vars\n\n"

    specification += "Spec == Init /\\ [][Next]_vars\n===="

    with open(MODEL_PATH, "w", encoding="utf-8") as f:
        f.write(specification)

    config = "SPECIFICATION Spec\nCHECK_DEADLOCK FALSE\nPROPERTY IsDAG"

    # TODO: Add reachable configs creation

    for prop in input_data.properties:
        if prop.type is not PropertyType.REACHABLE:
            config += f"\nINVARIANT {prop.id}"

    with open(SAFETY_CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write(config)


def _compile_init(init: dict[str, str | bool | int]) -> str:
    init_str = "Init =="
    for expr in init:
        init_str += f" /\\ {expr} = "
        if isinstance(init[expr], bool):
            init_str += "TRUE" if init[expr] else "FALSE"
        else:
            init_str += (
                f'"{init[expr]}"' if isinstance(init[expr], str) else str(init[expr])
            )
    init_str += "\n\n"
    return init_str


def _compile_variables(var_names: set[str]) -> str:
    variables_str = "VARIABLES "
    variables_str += ", ".join(var_names)
    variables_str += "\n\nvars == <<"
    variables_str += ", ".join(var_names)
    variables_str += ">>\n\n"
    return variables_str


def _compile_transitions(transitions: list[Transition], var_names: set[str]) -> str:
    transitions_str = ""
    for transition in transitions:
        transitions_str += _compile_transition(transition, var_names)
    transitions_str += "Next == \\/ "
    transitions_str += " \\/ ".join(transition.name for transition in transitions)
    transitions_str += "\n\n"
    return transitions_str


def _compile_transition(transition: Transition, var_names: set[str]) -> str:
    transition_str = transition.name + " =="

    if transition.guard:
        transition_str += " /\\ " + _compile_expression(transition.guard)

    for effect in transition.effects:
        transition_str += " /\\ "
        transition_str += _compile_effect(effect)

    changed_vars = set(effect.set_[0] for effect in transition.effects)
    transition_str += " /\\ UNCHANGED <<{}>>\n\n".format(
        ", ".join(var_names - changed_vars)
    )
    return transition_str


def _compile_properties(properties: list[Property]) -> str:
    properties_str = ""
    for prop in properties:
        match prop.type:
            case PropertyType.INVARIANT:
                properties_str += (
                    f"{prop.id} == ({_compile_expression(prop.expression)})\n\n"
                )
            case PropertyType.FORBIDDEN_STATE:
                properties_str += (
                    f"{prop.id} == ~({_compile_expression(prop.expression)})\n\n"
                )
            case PropertyType.DEADLOCK_FREE:
                properties_str += f"{prop.id} == ENABLED Next \\/ ({_compile_expression(prop.expression)})\n\n"
            case PropertyType.REACHABLE:
                properties_str += (
                    f"{prop.id} == ~[](~({_compile_expression(prop.expression)}))\n\n"
                )
    return properties_str


type BinaryExpression = Eq | Ne | Lt | Le | Gt | Ge | Add | Sub


def _compile_binary_expression(expression: BinaryExpression) -> str:
    match expression:
        case Eq(eq=(left, right)):
            symbol = "="
        case Ne(ne=(left, right)):
            symbol = "#"
        case Lt(lt=(left, right)):
            symbol = "<"
        case Le(le=(left, right)):
            symbol = "<="
        case Gt(gt=(left, right)):
            symbol = ">"
        case Ge(ge=(left, right)):
            symbol = ">="
        case Add(add=(left, right)):
            symbol = "+"
        case Sub(sub=(left, right)):
            symbol = "-"
        case _:
            raise TypeError(
                f"Unsupported binary expression: {type(expression).__name__}"
            )
    return f"(({_compile_expression(left)}) {symbol} ({_compile_expression(right)}))"


def _compile_expression(expression: ExpressionArgument) -> str:
    if isinstance(expression, VariableRef):
        return expression.var
    if isinstance(expression, str | bool | int):
        if isinstance(expression, bool):
            return "TRUE" if expression else "FALSE"
        return f'"{expression}"' if isinstance(expression, str) else str(expression)
    if isinstance(expression, In):
        if isinstance(expression.in_[1][0], str):
            set_elems = ", ".join(f'"{elem}"' for elem in expression.in_[1])
        else:
            set_elems = ", ".join(str(elem) for elem in expression.in_[1])
        return f"({_compile_expression(expression.in_[0])}) \\in {{{set_elems}}}"
    if isinstance(expression, And):
        return (
            "("
            + " /\\ ".join(_compile_expression(expr) for expr in expression.and_)
            + ")"
        )
    if isinstance(expression, Or):
        return (
            "("
            + " \\/ ".join(_compile_expression(expr) for expr in expression.or_)
            + ")"
        )
    if isinstance(expression, Not):
        return f"~({_compile_expression(expression.not_[0])})"

    if isinstance(expression, Eq | Ne | Lt | Le | Gt | Ge | Add | Sub):
        return _compile_binary_expression(expression)

    raise TypeError(f"Unsupported expression type: {type(expression).__name__}")


def _compile_effect(effect: Effect) -> str:
    return f"{effect.set_[0]}' = {_compile_expression(effect.set_[1])}"


# TODO TypeOK

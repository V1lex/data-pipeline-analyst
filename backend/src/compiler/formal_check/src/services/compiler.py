from pathlib import Path

from compiler.formal_check.src.config import MODEL_NAME, MODEL_PATH, SAFETY_CONFIG_PATH
from compiler.formal_check.src.models.input import (
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
    VariableType,
)
from compiler.formal_check.src.services.strings import encode_string


def compile_input(input_data: Input, *, output_dir: Path | str | None = None) -> None:
    directory = Path(output_dir) if output_dir is not None else MODEL_PATH.parent
    directory.mkdir(parents=True, exist_ok=True)
    model_path = directory / MODEL_PATH.name
    safety_path = directory / SAFETY_CONFIG_PATH.name
    specification = f"---- MODULE {MODEL_NAME} ----\n"
    specification += "EXTENDS Integers\n\n"

    var_names = {variable.name for variable in input_data.variables}
    specification += _compile_variables(var_names)

    specification += _compile_init(input_data.initial)

    specification += _compile_transitions(input_data.transitions, var_names)

    specification += _compile_properties(input_data.properties)
    domains = []
    for variable in input_data.variables:
        if variable.type is VariableType.BOOLEAN:
            domains.append(f"{variable.name} \\in BOOLEAN")
        elif variable.type is VariableType.ENUM:
            domain = ", ".join(_compile_expression(value) for value in variable.values or [])
            domains.append(f"{variable.name} \\in {{{domain}}}")
        else:
            domains.append(f"{variable.name} \\in Int")
            if variable.min is not None:
                domains.append(f"{variable.name} >= {variable.min}")
            if variable.max is not None:
                domains.append(f"{variable.name} <= {variable.max}")
    specification += "TypeOK == " + " /\\ ".join(f"({domain})" for domain in domains) + "\n\n"

    specification += "Spec == Init /\\ [][Next]_vars\n===="

    with open(model_path, "w", encoding="utf-8") as f:
        f.write(specification)

    config = "SPECIFICATION Spec\nCHECK_DEADLOCK FALSE\nINVARIANT TypeOK"

    for prop in input_data.properties:
        if prop.type is not PropertyType.REACHABLE:
            config += f"\nINVARIANT {prop.id}"

    with open(safety_path, "w", encoding="utf-8") as f:
        f.write(config)
    reachable_dir = directory / "reachable"
    reachable_dir.mkdir(exist_ok=True)
    for prop in input_data.properties:
        if prop.type is PropertyType.REACHABLE:
            (reachable_dir / f"{prop.id}.cfg").write_text(
                f"SPECIFICATION Spec\nCHECK_DEADLOCK FALSE\nINVARIANT TypeOK\nINVARIANT {prop.id}\n",
                encoding="utf-8",
            )


def _compile_init(init: dict[str, str | bool | int]) -> str:
    init_str = "Init =="
    for expr, value in init.items():
        init_str += f" /\\ {expr} = "
        init_str += _compile_expression(value)
    init_str += "\n\n"
    return init_str


def _compile_variables(var_names: set[str]) -> str:
    variables_str = "VARIABLES "
    variables_str += ", ".join(sorted(var_names))
    variables_str += "\n\nvars == <<"
    variables_str += ", ".join(sorted(var_names))
    variables_str += ">>\n\n"
    return variables_str


def _compile_transitions(transitions: list[Transition], var_names: set[str]) -> str:
    transitions_str = ""
    for transition in transitions:
        transitions_str += _compile_transition(transition, var_names)
    transitions_str += "Next == "
    transitions_str += " \\/ ".join(transition.name for transition in transitions) or "FALSE"
    transitions_str += "\n\n"
    return transitions_str


def _compile_transition(transition: Transition, var_names: set[str]) -> str:
    transition_str = transition.name + " =="

    if transition.guard:
        transition_str += " /\\ " + _compile_expression(transition.guard)

    for effect in transition.effects:
        transition_str += " /\\ "
        transition_str += _compile_effect(effect)

    changed_vars = {effect.set_[0] for effect in transition.effects}
    transition_str += " /\\ UNCHANGED <<{}>>\n\n".format(
        ", ".join(sorted(var_names - changed_vars))
    )
    return transition_str


def _compile_properties(properties: list[Property]) -> str:
    properties_str = ""
    for prop in properties:
        match prop.type:
            case PropertyType.INVARIANT:
                properties_str += f"{prop.id} == ({_compile_expression(prop.expression)})\n\n"
            case PropertyType.FORBIDDEN_STATE:
                properties_str += f"{prop.id} == ~({_compile_expression(prop.expression)})\n\n"
            case PropertyType.DEADLOCK_FREE:
                properties_str += (
                    f"{prop.id} == ENABLED Next \\/ ({_compile_expression(prop.expression)})\n\n"
                )
            case PropertyType.REACHABLE:
                properties_str += f"{prop.id} == ~({_compile_expression(prop.expression)})\n\n"
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
            raise TypeError(f"Unsupported binary expression: {type(expression).__name__}")
    return f"(({_compile_expression(left)}) {symbol} ({_compile_expression(right)}))"


def _compile_expression(expression: ExpressionArgument) -> str:
    if isinstance(expression, VariableRef):
        return expression.var
    if isinstance(expression, str | bool | int):
        if isinstance(expression, bool):
            return "TRUE" if expression else "FALSE"
        return encode_string(expression) if isinstance(expression, str) else str(expression)
    if isinstance(expression, In):
        set_elems = ", ".join(_compile_expression(elem) for elem in expression.in_[1])
        return f"({_compile_expression(expression.in_[0])}) \\in {{{set_elems}}}"
    if isinstance(expression, And):
        return "(" + " /\\ ".join(_compile_expression(expr) for expr in expression.and_) + ")"
    if isinstance(expression, Or):
        return "(" + " \\/ ".join(_compile_expression(expr) for expr in expression.or_) + ")"
    if isinstance(expression, Not):
        return f"~({_compile_expression(expression.not_[0])})"

    if isinstance(expression, Eq | Ne | Lt | Le | Gt | Ge | Add | Sub):
        return _compile_binary_expression(expression)

    raise TypeError(f"Unsupported expression type: {type(expression).__name__}")


def _compile_effect(effect: Effect) -> str:
    return f"{effect.set_[0]}' = {_compile_expression(effect.set_[1])}"

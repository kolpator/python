# from fabric.api import cd, env, prefix, run, task
#
# env.hosts = ['my_server1', 'my_server2']
#
# @task
# def memory_usage():
#     run('free -m')
#
# @task
# def deploy():
#     with cd('/var/www/project-env/project'):
#         with prefix('. ../bin/activate'):
#             run('git pull')
#             run('touch app.wsgi')



while True:
    command = input('>').lower()
    if command == 'help':
       print('''
start - to start the car
stop - to stop the car
quit - to exit''')
    elif command == 'start':
        print('car started')
    elif command == 'stop':
        print('car stopped')
    elif command == 'quit':
        print('Program terminated')
        break
    else:
        print(' I dont understand that')